# SPDX-License-Identifier: Apache-2.0
"""Reading a model's answer out of the shapes chat models return it in.

A LangChain message's ``content`` is a string, or a list of content blocks:
plain strings, dicts such as ``{"type": "text", "text": "...", "extras":
{"signature": "..."}}``, reasoning blocks that carry no answer text, tool-use
blocks, or provider SDK objects with a ``text`` attribute. Parsers read the
blocks' text. They never take ``str()`` of a block: a dict's repr hides a
fenced JSON answer from the parser and leaks the block into the output.
"""

from __future__ import annotations

from typing import Any

# Blocks that hold the model's reasoning, never its answer, even if a provider adds a text field.
_REASONING_TYPES = frozenset({"thinking", "redacted_thinking", "reasoning"})


def block_text(block: Any) -> str | None:
    """The answer text one content block carries, or ``None`` when it carries none."""
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        if block.get("type") in _REASONING_TYPES:
            return None
        text = block.get("text")
    else:
        text = getattr(block, "text", None)
    return text if isinstance(text, str) else None


def content_text(content: Any) -> str:
    """The text of a model message's content.

    A string is returned as it is. A list of blocks gives each block's text,
    joined by newlines; blocks with no text (reasoning, signatures, tool use)
    are skipped. A single block gives its text, or an empty string. Any other
    value keeps its ``str()`` form.
    """
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list | tuple):
        return "\n".join(text for text in (block_text(block) for block in content) if text is not None)
    if isinstance(content, dict) or hasattr(content, "text"):
        return block_text(content) or ""
    return str(content)


def strip_code_fence(text: str) -> str:
    """``text`` stripped, without the markdown fence lines (including a ```json tag) when it opens with one."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = [line for line in stripped.split("\n") if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    return stripped

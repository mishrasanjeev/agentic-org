# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Reading a model's answer out of the shapes chat models return it in.

A LangChain message's ``content`` is a string, or a list of content blocks:
plain strings, dicts such as ``{"type": "text", "text": "...", "extras":
{"signature": "..."}}``, refusal blocks (``{"type": "refusal", "refusal":
"..."}``), reasoning blocks that carry no answer text, tool-use blocks, or
provider SDK objects with a ``text`` attribute. Parsers read the blocks' text.
They never take ``str()`` of a block: a dict's repr hides a fenced JSON answer
from the parser and leaks the block into the output.

The output parsers read block content this way while
``agent_output_block_parsing`` is on (the default; ``model_answer``). The
guardrail hooks read it with ``content_text`` and write a transform back with
``with_text`` whatever that setting says.
"""

from __future__ import annotations

from typing import Any

from core.config import settings

# Blocks that hold the model's reasoning, never its answer, even if a provider adds a text field.
_REASONING_TYPES = frozenset({"thinking", "redacted_thinking", "reasoning"})
# A refusal block carries the model's answer (why it declined) under this key instead of "text".
_REFUSAL_TYPE = "refusal"


def block_text(block: Any) -> str | None:
    """The answer text one content block carries, or ``None`` when it carries none."""
    if isinstance(block, str):
        return block
    if isinstance(block, dict):
        kind = block.get("type")
        if kind in _REASONING_TYPES:
            return None
        text = block.get(_REFUSAL_TYPE if kind == _REFUSAL_TYPE else "text")
    else:
        text = getattr(block, "text", None)
    return text if isinstance(text, str) else None


def _answer_part(block: Any) -> str | None:
    """A block's share of the joined answer: its text, or ``None`` for a block that adds nothing.

    A dict or object block with empty text (a signature-only text block) adds
    nothing; a plain string item is kept even when empty, so a list of strings
    joins exactly as it did before.
    """
    text = block_text(block)
    if text == "" and not isinstance(block, str):
        return None
    return text


def content_text(content: Any) -> str:
    """The text of a model message's content.

    A string is returned as it is. A list of blocks gives each block's text,
    joined by newlines; blocks with no text (reasoning, signatures, tool use,
    a signature-only text block with empty text) are skipped. A single block
    gives its text, or an empty string. Any other value keeps its ``str()``
    form.
    """
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if isinstance(content, list | tuple):
        return "\n".join(text for text in (_answer_part(block) for block in content) if text is not None)
    if isinstance(content, dict) or hasattr(content, "text"):
        return block_text(content) or ""
    return str(content)


def block_parsing_enabled() -> bool:
    """``agent_output_block_parsing``: on (the default), the output parsers read block content for its text."""
    return bool(settings.agent_output_block_parsing)


def model_answer(content: Any) -> Any:
    """What an output parser reads of a model message's content.

    Its text (``content_text``) while ``agent_output_block_parsing`` is on;
    with it off, the content exactly as given, as the parsers read it before.
    """
    return content_text(content) if block_parsing_enabled() else content


def with_text(content: Any, text: str) -> Any:
    """``content`` with its answer text replaced by ``text``, as a guardrail transform writes it back.

    Anything other than a list of blocks becomes ``text``. In a list, the
    first block that carries answer text takes ``text`` (a dict block keeps
    its other keys, a refusal block keeps its type and takes ``text`` as its
    refusal, an object block becomes a text block) and the other text-bearing
    blocks are dropped, since ``text`` stands for their joined text. Blocks
    that add nothing to the answer (reasoning, signature-only text blocks,
    tool use, images) stay where they were, unchanged. A list with no text
    block gains one at the end.
    """
    if not isinstance(content, list | tuple):
        return text
    out: list[Any] = []
    placed = False
    for block in content:
        if _answer_part(block) is None:
            out.append(block)
        elif not placed:
            placed = True
            if isinstance(block, str):
                out.append(text)
            elif isinstance(block, dict):
                key = _REFUSAL_TYPE if block.get("type") == _REFUSAL_TYPE else "text"
                out.append({**block, key: text})
            else:
                out.append({"type": "text", "text": text})
    if not placed:
        out.append({"type": "text", "text": text})
    return out


def strip_code_fence(text: str) -> str:
    """``text`` stripped, without the markdown fence lines (including a ```json tag) when it opens with one."""
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = [line for line in stripped.split("\n") if not line.strip().startswith("```")]
        stripped = "\n".join(lines).strip()
    return stripped

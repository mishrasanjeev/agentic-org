#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""CI guard: agent prompts call only tools the agent actually has (PRD §7 F-3).

Checks, with no baseline:

* every tool a built-in prompt (``core/agents/prompts/*.prompt.txt``) tells the
  model to call — ``call x(...)``, ``x()`` or a snake_case ``x(...)`` outside
  the ``Token scope:`` block — is registered by a connector and is in that
  agent type's default tool list (``_AGENT_TYPE_DEFAULT_TOOLS``);
* every name in ``_AGENT_TYPE_DEFAULT_TOOLS`` and ``_DOMAIN_DEFAULT_TOOLS`` is
  registered, and a ``connector:tool`` name resolves to that connector.

A prompt maps to the agent type named by its file stem, or by the stem without
a trailing ``_agent`` (``abm_agent`` -> ``abm``). A prompt that calls a tool
but maps to no default tool list is a violation.

Fails closed: if the connector registry or the default lists cannot be loaded,
or the prompts directory is missing, the check exits 2 rather than passing.

Run locally:

    python scripts/check_prompt_tools.py
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = REPO_ROOT / "core" / "agents" / "prompts"
PROMPT_SUFFIX = ".prompt.txt"

_NAME = r"[A-Za-z_][A-Za-z0-9_]*(?::[A-Za-z0-9_]+)?"
_CALL_RE = re.compile(rf"\bcall\s+({_NAME})\s*\(", re.IGNORECASE)
_EMPTY_CALL_RE = re.compile(rf"(?<![\w.:])({_NAME})\(\)")
# snake_case name followed by "(" — not a token-scope permission list such as
# ``banking_api(w:queue_payment)``. Plain words (``abs(``) have no underscore.
_SNAKE_CALL_RE = re.compile(r"(?<![\w.:])([a-z][a-z0-9]*_[a-z0-9_]*(?::[a-z0-9_]+)?)\((?!\s*(?:r|w|r/w):)")
_TOKEN_SCOPE_RE = re.compile(r"^\s*token scope:", re.IGNORECASE)

REASON_UNREGISTERED = "tool_unregistered"
REASON_NOT_IN_DEFAULTS = "tool_not_in_agent_defaults"
REASON_NO_DEFAULTS = "agent_has_no_default_tools"
REASON_DEFAULT_UNREGISTERED = "default_tool_unregistered"


class CheckSetupError(RuntimeError):
    """The registry, defaults or prompts could not be loaded."""


@dataclass(frozen=True)
class ToolRef:
    line: int
    name: str


@dataclass(frozen=True)
class Violation:
    where: str
    name: str
    reason: str

    def __str__(self) -> str:
        return f"{self.where}: {self.name} ({self.reason})"


def extract_tool_refs(text: str) -> list[ToolRef]:
    """Return the tool calls a prompt directs, in order, without duplicates."""
    refs: list[ToolRef] = []
    seen: set[tuple[int, str]] = set()
    in_token_scope = False
    for number, line in enumerate(text.splitlines(), start=1):
        if _TOKEN_SCOPE_RE.match(line):
            in_token_scope = True
            continue
        if in_token_scope:
            # The scope block continues on indented lines until a blank line
            # or the first section tag.
            if line.strip() and line[:1].isspace() and not line.lstrip().startswith("<"):
                continue
            in_token_scope = False
        for pattern in (_CALL_RE, _EMPTY_CALL_RE, _SNAKE_CALL_RE):
            for match in pattern.finditer(line):
                key = (number, match.group(1))
                if key not in seen:
                    seen.add(key)
                    refs.append(ToolRef(number, match.group(1)))
    return refs


def agent_type_for_prompt(stem: str, agent_defaults: Mapping[str, Sequence[str]]) -> str | None:
    if stem in agent_defaults:
        return stem
    base = stem.removesuffix("_agent")
    return base if base != stem and base in agent_defaults else None


def _registered(name: str, bare_index: Mapping[str, object], alias_index: Mapping[str, tuple[str, str]]) -> bool:
    if ":" in name:
        connector = name.split(":", 1)[0]
        match = alias_index.get(name)
        return match is not None and match[0] == connector
    return name in bare_index


def _in_defaults(name: str, defaults: Iterable[str]) -> bool:
    if ":" in name:
        return name in defaults
    return any(default == name or default.split(":", 1)[-1] == name for default in defaults)


def check(
    *,
    prompts_dir: Path,
    agent_defaults: Mapping[str, Sequence[str]],
    domain_defaults: Mapping[str, Sequence[str]],
    bare_index: Mapping[str, object],
    alias_index: Mapping[str, tuple[str, str]],
) -> list[Violation]:
    if not prompts_dir.is_dir():
        raise CheckSetupError(f"prompts directory not found: {prompts_dir}")
    prompt_files = sorted(prompts_dir.glob(f"*{PROMPT_SUFFIX}"))
    if not prompt_files:
        raise CheckSetupError(f"no prompts found in {prompts_dir}")
    if not bare_index:
        raise CheckSetupError("connector tool registry is empty")

    violations: list[Violation] = []
    for owner, lists in (("agent_type", agent_defaults), ("domain", domain_defaults)):
        for key, tools in lists.items():
            for tool in tools:
                if not _registered(tool, bare_index, alias_index):
                    violations.append(Violation(f"{owner}:{key}", tool, REASON_DEFAULT_UNREGISTERED))

    for path in prompt_files:
        stem = path.name[: -len(PROMPT_SUFFIX)]
        agent_type = agent_type_for_prompt(stem, agent_defaults)
        defaults = list(agent_defaults.get(agent_type, [])) if agent_type else []
        for ref in extract_tool_refs(path.read_text(encoding="utf-8")):
            where = f"{path.name}:{ref.line}"
            if not _registered(ref.name, bare_index, alias_index):
                violations.append(Violation(where, ref.name, REASON_UNREGISTERED))
            elif agent_type is None:
                violations.append(Violation(where, ref.name, REASON_NO_DEFAULTS))
            elif not _in_defaults(ref.name, defaults):
                violations.append(Violation(where, ref.name, REASON_NOT_IN_DEFAULTS))
    return violations


def check_repository() -> list[Violation]:
    """Run the check against this repository's prompts, defaults and registry."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    try:
        import connectors  # noqa: F401, PLC0415 - registers native connectors
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS, _DOMAIN_DEFAULT_TOOLS  # noqa: PLC0415
        from core.langgraph.tool_adapter import _build_tool_index  # noqa: PLC0415

        bare_index = _build_tool_index()
        alias_index = _build_tool_index(include_connector_aliases=True)
    except Exception as exc:  # noqa: BLE001 - any load failure must fail the check, not pass it
        raise CheckSetupError(f"could not load registry or defaults: {type(exc).__name__}: {exc}") from exc
    return check(
        prompts_dir=PROMPTS_DIR,
        agent_defaults=_AGENT_TYPE_DEFAULT_TOOLS,
        domain_defaults=_DOMAIN_DEFAULT_TOOLS,
        bare_index=bare_index,
        alias_index=alias_index,
    )


def main() -> int:
    try:
        violations = check_repository()
    except CheckSetupError as exc:
        print(f"prompt tool check could not run: {exc}", file=sys.stderr)
        return 2
    if violations:
        print("Prompt and default tool references that cannot run:", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        print(
            "Rewrite the prompt to use a registered tool from the agent's default tools, or to work from the "
            "task input and escalate when data is missing. Do not add tools to the defaults to make this pass.",
            file=sys.stderr,
        )
        return 1
    print("prompt tool check: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())

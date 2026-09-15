# SPDX-License-Identifier: Apache-2.0
"""A-5: the policy engine never calls a model and never touches the network."""

from __future__ import annotations

import ast
import os
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from core.policy import EXAMPLES_DIR, evaluate, load_policies
from tests.unit.policy.test_policy_determinism import generate_cases

REPO_ROOT = Path(__file__).resolve().parents[3]
POLICY_PACKAGE = REPO_ROOT / "core" / "policy"

# Everything the policy package may import. A model client, HTTP client,
# database or LangChain import here is a design violation, not a style issue.
_ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "collections.abc",
        "dataclasses",
        "enum",
        "hashlib",
        "json",
        "math",
        "os",
        "pathlib",
        "prometheus_client",
        "re",
        "structlog",
        "typing",
        "yaml",
    }
)


class _ForbiddenCallError(AssertionError):
    pass


def _forbid(name: str):
    def _raise(*args: Any, **kwargs: Any) -> Any:
        raise _ForbiddenCallError(f"policy evaluation reached {name}")

    return _raise


def test_policy_modules_import_nothing_that_can_reach_a_model() -> None:
    offenders: list[str] = []
    for path in sorted(POLICY_PACKAGE.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif isinstance(node, ast.Call) and getattr(node.func, "id", None) == "__import__":
                offenders.append(f"{path.name}: dynamic __import__")
            for name in names:
                if name not in _ALLOWED_IMPORTS and not name.startswith("core.policy"):
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []


def test_importing_the_policy_package_loads_no_model_or_network_client() -> None:
    script = (
        "import sys, core.policy\n"
        "prefixes = ('langchain', 'langgraph', 'openai', 'anthropic', 'google.genai', 'httpx', 'requests',\n"
        "            'aiohttp', 'sqlalchemy', 'redis', 'core.llm', 'core.langgraph', 'core.model_replay')\n"
        "print(sorted(m for m in sys.modules if m.startswith(prefixes)))\n"
    )
    completed = subprocess.run(  # noqa: S603 - fixed interpreter and script
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        timeout=50,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip().splitlines()[-1] == "[]"


def test_evaluation_with_model_factories_and_network_forbidden(monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    import core.langgraph.llm_factory as llm_factory
    import core.llm.router as router

    monkeypatch.setattr(llm_factory, "create_chat_model", _forbid("create_chat_model"))
    monkeypatch.setattr(llm_factory, "_create_live_chat_model", _forbid("_create_live_chat_model"))
    monkeypatch.setattr(llm_factory, "_build_model", _forbid("_build_model"))
    monkeypatch.setattr(router.LLMRouter, "__init__", _forbid("LLMRouter"))
    monkeypatch.setattr(router.SmartLLMRouter, "__init__", _forbid("SmartLLMRouter"))
    monkeypatch.setattr(httpx.Client, "send", _forbid("httpx.Client.send"))
    monkeypatch.setattr(httpx.AsyncClient, "send", _forbid("httpx.AsyncClient.send"))
    monkeypatch.setattr(socket.socket, "connect", _forbid("socket.connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", _forbid("socket.connect_ex"))
    monkeypatch.setattr(socket, "create_connection", _forbid("socket.create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", _forbid("socket.getaddrinfo"))

    # The forbidding patches really are in force.
    with pytest.raises(_ForbiddenCallError):
        socket.create_connection(("192.0.2.1", 443))
    with pytest.raises(_ForbiddenCallError):
        llm_factory.create_chat_model(model="any")

    policies = load_policies(EXAMPLES_DIR)
    for case in generate_cases(count=200):
        for policy in policies.values():
            evaluate(policy, case)

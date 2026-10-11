# SPDX-License-Identifier: Apache-2.0
"""What a metered call carries to the spend meter: cheap context variables, no I/O.

**Scopes.** An entry point (an agent run, a chat turn, a workflow step, a
content service) binds a ``SpendScope`` for the span of its work: the
application, the agent, the run, the workflow and the user who started it.
The model-call hook reads it. The first binder wins: a nested scope only
fills fields still empty, so the speech summary's ``application="speech"``
survives the ``application="content"`` the content service binds inside it.
Values are ids, codes and labels only, bounded in length. The labels a
caller sends in a request body are never bound here (attribution is
resolved on the server, ``core/spend/resolver.py``).

**Call usage.** A call site hands ``record_model_call`` a ``CallUsage`` (the
response, the error, the messages, the model object); nothing is read from
it on the call path. The meter reads it later inside its own guard.

**Credential source.** The router notes whether the tenant's own key or the
platform's paid for the call, for the billing account of its records.

Everything is a no-op while spend intelligence is off: a bind returns
``None``, a scope is a ``nullcontext``, ``call_usage`` returns ``None``.
This module imports ``core.config`` only.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager
from contextvars import ContextVar, Token
from dataclasses import dataclass, fields, replace
from typing import Any

from core.config import settings

_FIELD_MAX = 128
_SCOPE_FIELDS = (
    "tenant_id",
    "application",
    "default_use_case",
    "agent_id",
    "agent_version",
    "run_id",
    "workflow_id",
    "workflow_run_id",
    "initiating_user_id",
)
_CALL_USAGE_FIELDS = ("response", "error", "messages", "llm", "tenant_id", "billing_account")
_IN_HOUSE = frozenset({"ollama", "vllm", "local_embeddings", "tei", "tesseract", "faster_whisper"})
_SOURCE_ACCOUNT = {"tenant": "tenant_key", "platform_env": "platform_key"}
_PROVIDER_ALIASES = {"claude": "anthropic", "gpt": "openai"}


def _on() -> bool:
    return bool(getattr(settings, "spend_intelligence_enabled", False))


@dataclass(frozen=True)
class SpendScope:
    tenant_id: str | None = None
    application: str | None = None
    default_use_case: str | None = None
    agent_id: str | None = None
    agent_version: str | None = None
    run_id: str | None = None
    workflow_id: str | None = None
    workflow_run_id: str | None = None
    initiating_user_id: str | None = None


@dataclass(frozen=True)
class CallUsage:
    """What a call site hands ``record_model_call``; read only inside the meter's guard."""

    source: str  # "router" | "message"
    response: Any = None
    error: BaseException | None = None
    messages: Any = None
    llm: Any = None
    tenant_id: str | None = None
    billing_account: str | None = None


_SCOPE: ContextVar[SpendScope | None] = ContextVar("agenticorg_spend_scope", default=None)
_CREDENTIAL: ContextVar[tuple[str, str] | None] = ContextVar("agenticorg_spend_credential", default=None)


def _bounded(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:_FIELD_MAX] or None


def current_scope() -> SpendScope | None:
    """The scope bound for the current task, or ``None``."""
    return _SCOPE.get()


def _merged(fields_in: Mapping[str, Any]) -> SpendScope:
    current = _SCOPE.get() or SpendScope()
    updates = {}
    for name in _SCOPE_FIELDS:
        if getattr(current, name) is None and name in fields_in:
            value = _bounded(fields_in[name])
            if value is not None:
                updates[name] = value
    return replace(current, **updates) if updates else current


def bind_scope(**fields_in: Any) -> Token[SpendScope | None] | None:
    """Bind a scope for the current task (first binder wins per field); ``None`` (a no-op) while off.

    Unknown field names are ignored, so a binding can never fail the entry point that makes it.
    """
    if not _on():
        return None
    return _SCOPE.set(_merged(fields_in))


def reset_scope(token: Token[SpendScope | None] | None) -> None:
    """Undo ``bind_scope``; ``None`` does nothing."""
    if token is not None:
        _SCOPE.reset(token)


@contextlib.contextmanager
def _scoped(fields_in: dict[str, Any]) -> Iterator[None]:
    token = bind_scope(**fields_in)
    try:
        yield
    finally:
        reset_scope(token)


def scope(**fields_in: Any) -> AbstractContextManager[None]:
    """A context manager binding a scope for its block; ``nullcontext()`` while off."""
    if not _on():
        return contextlib.nullcontext()
    return _scoped(fields_in)


def call_usage(source: str, **fields_in: Any) -> CallUsage | None:
    """A ``CallUsage`` for ``record_model_call``; ``None`` while off. Never raises."""
    if not _on():
        return None
    try:
        known = {name: fields_in[name] for name in _CALL_USAGE_FIELDS if name in fields_in}
        if known.get("tenant_id") is not None:
            known["tenant_id"] = _bounded(known["tenant_id"])
        return CallUsage(source=str(source), **known)
    # enterprise-gate: broad-except-ok reason=spend-usage-capture-failure-degrades-to-no-details-the-call-proceeds
    except Exception:
        return None


def _provider_id(provider: Any) -> str:
    key = str(provider or "").strip().lower()
    return _PROVIDER_ALIASES.get(key, key)


def note_credential(provider: str, source: str) -> None:
    """Remember which credential (``tenant`` or ``platform_env``) paid for this task's calls to ``provider``."""
    if not _on():
        return
    _CREDENTIAL.set((_provider_id(provider), str(source or "").strip().lower()))


def current_credential() -> tuple[str, str] | None:
    """``(provider, "tenant" | "platform_env")`` noted in this task, or ``None``."""
    return _CREDENTIAL.get()


def account_for(provider: str, credential: tuple[str, str] | None) -> str | None:
    """The billing account a noted credential gives ``provider``'s records, or ``None`` when it names another."""
    if credential is None or credential[0] != _provider_id(provider):
        return None
    return _SOURCE_ACCOUNT.get(credential[1])


def billing_account_of(
    snapshot: Mapping[tuple[str, str], object] | None, tenant_id: str | None, model: str, provider: str | None
) -> str | None:
    """The billing account of a graph's calls from the credential the runner prefetched. Never raises.

    ``in_house`` for a local model; the prefetched ``ResolvedCredential``'s
    source for a cloud one (``tenant_key`` or ``platform_key``); ``None``
    when unknown (the writer infers it). ``None`` at once while off.
    """
    if not _on():
        return None
    try:
        declared = _provider_id(provider)
        if declared in _IN_HOUSE:
            return "in_house"
        from core.langgraph.llm_factory import _infer_cloud_provider

        cloud = _infer_cloud_provider(model or "", provider or None)
        if cloud is None:
            return "in_house"
        if not snapshot or not tenant_id:
            return None
        credential = snapshot.get((str(tenant_id), str(cloud)))
        return _SOURCE_ACCOUNT.get(str(getattr(credential, "source", "") or ""))
    # enterprise-gate: broad-except-ok reason=billing-account-capture-failure-degrades-to-writer-inference
    except Exception:
        return None


def scope_fields() -> tuple[str, ...]:
    """The fields a scope carries."""
    return tuple(f.name for f in fields(SpendScope))

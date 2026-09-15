# SPDX-License-Identifier: Apache-2.0
"""Tenant-scoped checkpoint thread ids.

The LangGraph checkpoint tables have no ``tenant_id`` column, so row-level
security cannot protect them. Isolation instead rests on the thread id:

* the server generates it, prefixed with the run's tenant:
  ``tenant:<tenant uuid>:run:<32 hex chars of randomness>``;
* it is stored only on the RLS-protected ``hitl_queue`` row, which also
  enforces the prefix with a check constraint;
* nothing accepts a thread id from a client, and a resume refuses a thread
  whose prefix is not the caller's tenant.

Internal callers that pass their own thread id (voice sessions) have it
namespaced under their tenant, so no run can write outside its tenant's prefix.
"""

from __future__ import annotations

import re
import secrets
import uuid

THREAD_PREFIX = "tenant:"
_SUFFIX_RE = re.compile(r"[A-Za-z0-9._:\-]{1,160}")
_SCOPED_RE = re.compile(
    r"tenant:(?P<tenant>[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}):(?P<suffix>[A-Za-z0-9._:\-]{1,160})"
)


class CheckpointThreadError(ValueError):
    """A thread id cannot be scoped to, or does not belong to, the tenant. ``reason`` is a stable code."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"agent checkpoint thread refused: {reason}")


def canonical_tenant_id(tenant_id: str | uuid.UUID | None) -> str | None:
    """Lower-case hyphenated UUID, or ``None`` when ``tenant_id`` is not a UUID."""
    if isinstance(tenant_id, uuid.UUID):
        return str(tenant_id)
    try:
        return str(uuid.UUID(str(tenant_id or "")))
    except ValueError:
        return None


def new_thread_id(tenant_id: str | uuid.UUID) -> str:
    """Generate a fresh thread id for one run of ``tenant_id``."""
    tenant = canonical_tenant_id(tenant_id)
    if tenant is None:
        raise CheckpointThreadError("checkpoint_thread_tenant_invalid")
    return f"{THREAD_PREFIX}{tenant}:run:{secrets.token_hex(16)}"


def thread_tenant(thread_id: str | None) -> str | None:
    """The tenant a well-formed scoped thread id belongs to, else ``None``."""
    match = _SCOPED_RE.fullmatch(thread_id or "")
    return match.group("tenant") if match else None


def thread_belongs_to_tenant(thread_id: str | None, tenant_id: str | uuid.UUID | None) -> bool:
    tenant = canonical_tenant_id(tenant_id)
    return tenant is not None and thread_tenant(thread_id) == tenant


def scoped_thread_id(tenant_id: str | uuid.UUID, thread_id: str | None) -> str:
    """Return ``thread_id`` scoped to ``tenant_id``: generated when absent, namespaced when unscoped.

    A thread id already scoped to a *different* tenant is refused rather than
    re-namespaced, since only a bug or a forged value can produce one.
    """
    tenant = canonical_tenant_id(tenant_id)
    if tenant is None:
        raise CheckpointThreadError("checkpoint_thread_tenant_invalid")
    if not thread_id:
        return new_thread_id(tenant)
    owner = thread_tenant(thread_id)
    if owner == tenant:
        return thread_id
    if owner is not None or thread_id.startswith(THREAD_PREFIX):
        raise CheckpointThreadError("checkpoint_thread_tenant_mismatch")
    if not _SUFFIX_RE.fullmatch(thread_id):
        raise CheckpointThreadError("checkpoint_thread_id_invalid")
    return f"{THREAD_PREFIX}{tenant}:{thread_id}"

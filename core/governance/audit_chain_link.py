# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The audit chain's link arithmetic, with no dependency beyond the standard library.

``canonical_audit_payload`` is the serialisation the platform audit key signs
(``core.tool_gateway.audit_logger``) and ``link_hash`` is the chain link the
sealing task stores (``core.governance.audit_chain``). Both live here so the
offline ledger verifier (``scripts/verify_ledger.py``) can recompute a chain
from an export file without importing settings, a database driver or network
code: it loads this file by path.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

CHAIN_VERSION = "1"
GENESIS = "0" * 64

# Columns covered by the row signature, in canonical order. ``id`` is
# excluded on purpose: the DB generates it, so signing a pre-insert random id
# produced a signature nothing could ever verify.
SIGNED_COLUMNS: tuple[str, ...] = (
    "tenant_id",
    "event_type",
    "actor_type",
    "actor_id",
    "agent_id",
    "workflow_run_id",
    "resource_type",
    "resource_id",
    "action",
    "outcome",
    "details",
    "trace_id",
    "created_at",
)


def canonical_value(key: str, value: Any) -> Any:
    """Normalise a column value so the same row hashes identically whether it
    comes from the in-memory entry (str/ISO) or an ORM row (UUID/datetime)."""
    if value is None:
        return None
    if key == "created_at":
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=UTC)
            return value.astimezone(UTC).isoformat()
        return str(value)
    if key == "details":
        return value if isinstance(value, dict) else {}
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def canonical_audit_payload(record: Any) -> str:
    """Serialise the signed columns of ``record`` (dict or ``AuditLog`` row)."""
    get = record.get if isinstance(record, dict) else lambda k, d=None: getattr(record, k, d)
    canonical = {key: canonical_value(key, get(key)) for key in SIGNED_COLUMNS}
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)


def link_hash(prev_hash: str, row: Any) -> str:
    """The link for ``row`` after ``prev_hash``: SHA-256 over the version, that link, the payload and the signature."""
    signature = row.get("signature") if isinstance(row, dict) else getattr(row, "signature", None)
    material = f"{CHAIN_VERSION}|{prev_hash}|{canonical_audit_payload(row)}|{str(signature or '')}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()

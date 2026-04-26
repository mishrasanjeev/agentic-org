"""Connector endpoints."""

from __future__ import annotations

import ipaddress
import logging
import socket
import uuid as _uuid
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.deps import get_current_tenant, require_tenant_admin
from core.database import get_tenant_session
from core.models.connector import Connector
from core.schemas.api import ConnectorCreate, ConnectorUpdate

_log = logging.getLogger(__name__)


def _assert_public_base_url(base_url: str) -> None:
    """Reject connector base_urls that resolve to private/reserved ranges.

    SECURITY_AUDIT-2026-04-19 MEDIUM-12: admins could previously set any
    base_url on a connector, turning connector test/health flows into an
    SSRF primitive against the cluster's internal network (including
    169.254.169.254 cloud metadata).

    Unresolvable hosts are allowed through (DNS may be environment-
    specific — CI cannot prove a production host is bad); only explicit
    resolution to a private/reserved range is blocked.
    """
    if not base_url:
        return
    parsed = urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(400, f"Connector base_url must be http(s): got '{parsed.scheme}'")
    host = parsed.hostname or ""
    if not host:
        raise HTTPException(400, "Connector base_url is missing a host")

    # Reject bare IPs in private/reserved ranges without needing DNS.
    try:
        literal_ip: ipaddress._BaseAddress | None = ipaddress.ip_address(host)
    except ValueError:
        literal_ip = None
    candidates: list[str] = []
    if literal_ip is not None:
        candidates.append(str(literal_ip))
    else:
        try:
            infos = socket.getaddrinfo(host, None)
            candidates = [info[4][0] for info in infos]
        except socket.gaierror:
            # Unresolvable — httpx will error on the real call. Don't
            # block registration purely on a transient DNS miss.
            return
    for addr in candidates:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            raise HTTPException(
                403,
                f"Connector base_url '{host}' resolves to a blocked "
                f"address ({ip}). Private, loopback, link-local, "
                "multicast, reserved, and unspecified ranges are not "
                "allowed.",
            )

router = APIRouter(dependencies=[require_tenant_admin])


def _connector_to_dict(conn: Connector) -> dict:
    # Return a boolean flag for whether credentials are configured —
    # NEVER return the actual auth_config (secrets) in the API response.
    has_creds = bool(conn.auth_config) or bool(getattr(conn, "secret_ref", None))
    return {
        "id": str(conn.id),
        "connector_id": str(conn.id),  # kept for backward compat
        "name": conn.name,
        "category": conn.category,
        "description": conn.description,
        "base_url": conn.base_url,
        "auth_type": conn.auth_type,
        "has_credentials": has_creds,
        "tool_functions": conn.tool_functions,
        "data_schema_ref": conn.data_schema_ref,
        "rate_limit_rpm": conn.rate_limit_rpm,
        "timeout_ms": conn.timeout_ms,
        "status": conn.status,
        "health_check_at": conn.health_check_at.isoformat() if conn.health_check_at else None,
        "created_at": conn.created_at.isoformat() if conn.created_at else None,
    }


# ── GET /connectors/registry ────────────────────────────────────────────────
@router.get("/connectors/registry")
async def list_registry_connectors(category: str | None = None):
    """Return the native connector catalog from the runtime registry.

    Each item combines the registry truth (class-level `name`, `category`,
    `auth_type`, `tools`, etc.) with display metadata from
    `connectors/catalog_meta.py`. The UI consumes this via PR-B2 instead
    of a hardcoded `NATIVE_CONNECTOR_CATALOG` array — adding / renaming /
    re-categorising a native connector no longer requires a UI code edit.
    """
    import connectors  # noqa: F401, F811
    from connectors.catalog_meta import get_meta
    from connectors.registry import ConnectorRegistry

    if category:
        classes = ConnectorRegistry.by_category(category)
    else:
        classes = [
            ConnectorRegistry.get(name)
            for name in ConnectorRegistry.all_names()
            if ConnectorRegistry.get(name) is not None
        ]

    items = []
    for cls in classes:
        tools = []
        if hasattr(cls, "tools") and cls.tools:
            tools = [t if isinstance(t, str) else getattr(t, "name", str(t)) for t in cls.tools]
        meta = get_meta(cls.name)
        items.append({
            "id": f"registry-{cls.name}",
            "connector_id": f"registry-{cls.name}",
            "name": cls.name,
            "display_name": meta["display_name"],
            "category": cls.category,
            "description": meta["description"],
            "base_url": cls.base_url,
            "auth_type": cls.auth_type,
            "tool_functions": tools,
            "rate_limit_rpm": cls.rate_limit_rpm,
            "timeout_ms": cls.timeout_ms,
            "status": "available",
            "health_check_at": None,
            "created_at": None,
        })

    # Deterministic ordering so the UI is stable across renders.
    items.sort(key=lambda x: x["display_name"].lower())

    return {"items": items, "total": len(items)}


# ── GET /tools — Function-level tool names ─────────────────────────────────
@router.get("/tools")
async def list_tools(
    category: str | None = None,
    connectors: str | None = None,
):
    """Return all function-level tool names from the connector registry.

    Unlike ``GET /mcp/tools`` which returns agent-level MCP tool names
    (e.g., ``agenticorg_email_agent``), this returns the actual function-level
    tool names (e.g., ``send_email``, ``read_inbox``) that agents use.

    Query params
    ------------
    ``category``
        Optional — only return tools whose connector's category matches.
    ``connectors``
        Optional — comma-separated list of connector names. Tools are
        restricted to those belonging to the named connectors. Used by
        the agent creation UI (UR-Bug-2) so ``authorized_tools`` picker
        matches the connectors the user actually selected.
    """
    connector_filter: list[str] | None = None
    if connectors:
        connector_filter = [
            part.strip() for part in connectors.split(",") if part.strip()
        ] or None

    try:
        from core.langgraph.tool_adapter import _build_tool_index

        tool_index = _build_tool_index(connector_names=connector_filter)
        tools: list[str] = []
        tool_details: list[dict] = []
        for tool_name, (connector_name, description) in sorted(tool_index.items()):
            if category:
                # Filter by connector category if requested
                from connectors.registry import ConnectorRegistry

                cls = ConnectorRegistry.get(connector_name)
                if cls and cls.category != category:
                    continue
            tools.append(tool_name)
            tool_details.append({
                "name": tool_name,
                "connector": connector_name,
                "description": description or f"{tool_name} on {connector_name}",
            })
        return {
            "tools": tools,
            "details": tool_details,
            "total": len(tools),
        }
    except Exception:
        # Fallback: extract from _AGENT_TYPE_DEFAULT_TOOLS
        from api.v1.agents import _AGENT_TYPE_DEFAULT_TOOLS

        all_tools: set[str] = set()
        for tools_list in _AGENT_TYPE_DEFAULT_TOOLS.values():
            all_tools.update(tools_list)
        sorted_tools = sorted(all_tools)
        return {
            "tools": sorted_tools,
            "details": [{"name": t, "connector": "unknown", "description": t} for t in sorted_tools],
            "total": len(sorted_tools),
        }


# ── GET /connectors ─────────────────────────────────────────────────────────
@router.get("/connectors")
async def list_connectors(
    category: str | None = None,
    page: int = 1,
    per_page: int = 50,
    tenant_id: str = Depends(get_current_tenant),
):
    from sqlalchemy import func

    if page < 1:
        raise HTTPException(422, "page must be >= 1")
    per_page = min(max(per_page, 1), 100)
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        # Uday 2026-04-23: soft-deleted connectors ("archived") must
        # stay out of the default listing, otherwise the Connectors
        # page continues to show them and the user can't distinguish
        # "archived, can recreate" from "active". Filter on status to
        # hide them by default; they re-enter on re-registration (the
        # POST /connectors auto-restore path).
        query = select(Connector).where(
            Connector.tenant_id == tid,
            func.coalesce(Connector.status, "active") != "deleted",
        )
        count_query = (
            select(func.count())
            .select_from(Connector)
            .where(
                Connector.tenant_id == tid,
                func.coalesce(Connector.status, "active") != "deleted",
            )
        )
        if category:
            query = query.where(Connector.category == category)
            count_query = count_query.where(Connector.category == category)
        total = (await session.execute(count_query)).scalar() or 0
        query = query.offset((page - 1) * per_page).limit(per_page)
        result = await session.execute(query)
        connectors = result.scalars().all()
    return {
        "items": [_connector_to_dict(c) for c in connectors],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


# ── POST /connectors ────────────────────────────────────────────────────────
@router.post("/connectors", status_code=201)
async def register_connector(
    body: ConnectorCreate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)

    # MEDIUM-12: reject SSRF-capable base_urls before persisting.
    _assert_public_base_url(body.base_url or "")

    # Separate secret material from non-secret config.
    # auth_config on the Connector model is deprecated for secrets —
    # new writes go to connector_configs.credentials_encrypted via
    # tenant-aware encryption.
    secret_fields = body.auth_config or {}
    non_secret_config = {
        "base_url": body.base_url,
        "auth_type": body.auth_type,
        "data_schema_ref": body.data_schema_ref,
        "rate_limit_rpm": body.rate_limit_rpm,
    }

    async with get_tenant_session(tid) as session:
        connector = Connector(
            tenant_id=tid,
            name=body.name,
            category=body.category,
            base_url=body.base_url,
            auth_type=body.auth_type,
            auth_config={},  # no longer store secrets here
            secret_ref=body.secret_ref,
            tool_functions=body.tool_functions,
            data_schema_ref=body.data_schema_ref,
            rate_limit_rpm=body.rate_limit_rpm,
            status="active",
        )
        session.add(connector)
        try:
            await session.flush()
        except IntegrityError:
            # Uday 2026-04-23: a row with the same (tenant_id, name) may
            # already exist. If that row is status="deleted" (soft-delete
            # via DELETE /connectors/{id}), the prior flow surfaced a
            # hard 409 "already exists" — confusing, because the user
            # had just archived it and expected re-registration to
            # work. Instead, reactivate the soft-deleted twin in place
            # so audit history is preserved on a single row. If the
            # existing row is still active, we still 409 honestly.
            existing = None
            try:
                await session.rollback()
                existing_result = await session.execute(
                    select(Connector).where(
                        Connector.tenant_id == tid,
                        Connector.name == body.name,
                    )
                )
                candidate = existing_result.scalar_one_or_none()
                # Guard: real ORM row will have a .status string; a
                # broken / mock Result may return a coroutine-like
                # object. Only treat as an existing row when we can
                # safely read .status.
                if hasattr(candidate, "status") and isinstance(candidate.status, str | type(None)):
                    existing = candidate
            except Exception:
                existing = None

            if existing is not None and (existing.status or "").lower() == "deleted":
                existing.status = "active"
                existing.category = body.category
                existing.base_url = body.base_url
                existing.auth_type = body.auth_type
                existing.auth_config = {}
                existing.secret_ref = body.secret_ref
                existing.tool_functions = body.tool_functions
                existing.data_schema_ref = body.data_schema_ref
                existing.rate_limit_rpm = body.rate_limit_rpm
                connector = existing
                await session.flush()
            else:
                raise HTTPException(
                    409, f"Connector '{body.name}' already exists"
                ) from None

        # Store secrets in the encrypted connector_configs table
        if secret_fields:
            from core.crypto import encrypt_for_tenant
            from core.models.connector_config import ConnectorConfig

            encrypted_creds = await encrypt_for_tenant(
                __import__("json").dumps(secret_fields), tid
            )
            # Uday 2026-04-26 (BUG 1): connector_configs has its own
            # UniqueConstraint(tenant_id, connector_name). When the
            # Connector was reactivated above, an old ConnectorConfig
            # row for the same name still exists — adding a new one
            # raises IntegrityError unhandled and the API returns 500
            # (UI shows "Failed to register connector. Please try
            # again."). Upsert in place: if a config exists, replace
            # its credentials + config + auth_type; otherwise add new.
            cc_existing_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == body.name,
                )
            )
            cc_existing = cc_existing_result.scalar_one_or_none()
            if cc_existing is not None:
                cc_existing.credentials_encrypted = {"_encrypted": encrypted_creds}
                cc_existing.config = non_secret_config
                cc_existing.auth_type = body.auth_type or "api_key"
                cc_existing.status = "configured"
            else:
                cc = ConnectorConfig(
                    tenant_id=tid,
                    connector_name=body.name,
                    display_name=body.name,
                    auth_type=body.auth_type or "api_key",
                    credentials_encrypted={"_encrypted": encrypted_creds},
                    config=non_secret_config,
                    status="configured",
                )
                session.add(cc)
            await session.flush()

    return _connector_to_dict(connector)


# ── GET /connectors/{conn_id} ────────────────────────────────────────────────
@router.get("/connectors/{conn_id}")
async def get_connector(
    conn_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(
                Connector.id == conn_id, Connector.tenant_id == tid
            )
        )
        connector = result.scalar_one_or_none()
    if not connector:
        raise HTTPException(404, "Connector not found")
    return _connector_to_dict(connector)


# ── PUT /connectors/{conn_id} ──────────────────────────────────────────────
@router.put("/connectors/{conn_id}")
async def update_connector(
    conn_id: UUID,
    body: ConnectorUpdate,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(
                Connector.id == conn_id, Connector.tenant_id == tid
            )
        )
        connector = result.scalar_one_or_none()
        if not connector:
            raise HTTPException(404, "Connector not found")

        # Prevent blind setattr on secret-bearing or internal fields.
        # auth_config is deprecated for new writes — secrets go via
        # connector_configs.credentials_encrypted.
        _blocked_fields = {"id", "tenant_id", "auth_config", "secret_ref"}
        updates = body.model_dump(exclude_none=True)
        # MEDIUM-12: same SSRF guard on update paths.
        if "base_url" in updates:
            _assert_public_base_url(updates["base_url"] or "")
        for field, value in updates.items():
            if field in _blocked_fields:
                continue
            setattr(connector, field, value)

        # If auth_config was provided in the update, MERGE it into the
        # existing encrypted credentials (PR #305 P0 fix 2026-04-24).
        #
        # Regression this guards against: the 24-Apr sweep added an
        # "Extra config (JSON)" textarea so admins could set Zoho Books
        # `organization_id`. A user editing only that field sent a
        # minimal `auth_config={"organization_id": "12345"}`. The old
        # code REPLACED `credentials_encrypted` with the encrypted
        # version of that small dict — wiping client_id / client_secret /
        # refresh_token. Next test-connection failed with "no creds".
        #
        # Fix: decrypt what's there, shallow-merge the new keys in
        # (new wins on collision), re-encrypt. Callers that genuinely
        # want to replace all creds still can — they just have to send
        # every key they intend to keep.
        new_secrets = body.model_dump(exclude_none=True).get("auth_config")
        if new_secrets:
            from core.crypto import encrypt_for_tenant
            from core.crypto.tenant_secrets import decrypt_for_tenant
            from core.models.connector_config import ConnectorConfig

            cc_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == connector.name,
                )
            )
            cc = cc_result.scalar_one_or_none()

            merged_secrets: dict = {}
            if cc and cc.credentials_encrypted:
                enc = cc.credentials_encrypted.get("_encrypted") if isinstance(cc.credentials_encrypted, dict) else None
                if isinstance(enc, str) and enc:
                    # Codex PR #305 review (P1): if the stored blob
                    # exists but we cannot decrypt it (key drift,
                    # corruption, transient error), MUST NOT fall back
                    # to treating the request as a full replacement.
                    # That was the original wipe bug. Fail-closed: refuse
                    # the write, keep credentials_encrypted untouched,
                    # return a 500 with a pointer to re-registration.
                    try:
                        existing_plain = decrypt_for_tenant(enc)
                        existing = __import__("json").loads(existing_plain)
                        if isinstance(existing, dict):
                            merged_secrets.update(existing)
                        else:
                            raise ValueError(
                                "decrypted credentials are not a JSON object"
                            )
                    except Exception as exc:
                        _log.exception(
                            "connector_update_decrypt_failed_refusing_wipe conn_id=%s",
                            conn_id,
                        )
                        raise HTTPException(
                            status_code=500,
                            detail=(
                                "Could not decrypt the existing connector "
                                "credentials. Refusing the update rather than "
                                "risk wiping them. Re-register the connector "
                                "via POST /connectors with the full credential "
                                "set (client_id, client_secret, refresh_token, "
                                "organization_id, etc.) to recover."
                            ),
                        ) from exc
            # New keys win on collision so admins can rotate creds.
            merged_secrets.update(new_secrets)

            encrypted = await encrypt_for_tenant(
                __import__("json").dumps(merged_secrets), tid
            )
            if cc:
                cc.credentials_encrypted = {"_encrypted": encrypted}
            else:
                new_cc = ConnectorConfig(
                    tenant_id=tid,
                    connector_name=connector.name,
                    display_name=connector.name,
                    auth_type=connector.auth_type or "api_key",
                    credentials_encrypted={"_encrypted": encrypted},
                    config={},
                    status="configured",
                )
                session.add(new_cc)

        await session.commit()
        await session.refresh(connector)

    return _connector_to_dict(connector)


# ── DELETE /connectors/{conn_id} ────────────────────────────────────────────
#
# Uday 2026-04-22: the connectors page listed instances but offered no
# way to remove a stale or misconfigured one. Callers were forced to
# issue raw SQL against the DB. Expose a soft-delete (sets status to
# "deleted") so the tool listing filters them out while preserving
# audit history. Hard delete is intentionally not supported — a
# deleted connector can be restored via PUT /connectors/{id}
# (status=active).
@router.delete("/connectors/{conn_id}", status_code=200)
async def delete_connector(
    conn_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(
                Connector.id == conn_id, Connector.tenant_id == tid
            )
        )
        connector = result.scalar_one_or_none()
        if not connector:
            raise HTTPException(404, "Connector not found")
        connector.status = "deleted"

    return {
        "connector_id": str(conn_id),
        "status": "deleted",
        "message": "Connector archived. Restore via PUT with status=active.",
    }


# ── GET /connectors/{conn_id}/health ─────────────────────────────────────────
@router.get("/connectors/{conn_id}/health")
async def connector_health(
    conn_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(Connector.id == conn_id, Connector.tenant_id == tid)
        )
        connector = result.scalar_one_or_none()

    if not connector:
        raise HTTPException(404, "Connector not found")

    return {
        "connector_id": str(connector.id),
        "name": connector.name,
        "status": connector.status,
        "health_check_at": connector.health_check_at.isoformat()
        if connector.health_check_at
        else None,
        "healthy": connector.status == "active",
    }


@router.post("/connectors/{conn_id}/test")
async def test_connector(
    conn_id: UUID,
    tenant_id: str = Depends(get_current_tenant),
):
    """Run a live connectivity test against the connector's API.

    Uses the stored credentials to connect, run a lightweight health
    probe, and return the result. Useful for verifying credentials
    after initial setup.
    """
    import asyncio

    from connectors.registry import ConnectorRegistry

    tid = _uuid.UUID(tenant_id)
    async with get_tenant_session(tid) as session:
        result = await session.execute(
            select(Connector).where(Connector.id == conn_id, Connector.tenant_id == tid)
        )
        connector = result.scalar_one_or_none()

    if not connector:
        raise HTTPException(404, "Connector not found")

    connector_cls = ConnectorRegistry.get(connector.name)
    if not connector_cls:
        return {"tested": False, "error": f"No connector class for '{connector.name}'"}

    # MEDIUM-12: even if the stored base_url was valid when saved, re-check
    # at test time so a stale DNS record flipped to a private IP can't be
    # used as an SSRF primitive.
    effective_base_url = (connector.base_url or getattr(connector_cls, "base_url", "") or "")
    _assert_public_base_url(effective_base_url)

    # Load credentials from ENCRYPTED connector_configs first,
    # falling back to legacy plaintext auth_config for backward compat.
    config: dict = {}
    try:
        from core.models.connector_config import ConnectorConfig

        async with get_tenant_session(tid) as session:
            cc_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tid,
                    ConnectorConfig.connector_name == connector.name,
                )
            )
            cc = cc_result.scalar_one_or_none()
            if cc and cc.credentials_encrypted:
                import json as _cjson

                creds = cc.credentials_encrypted
                if isinstance(creds, str):
                    creds = _cjson.loads(creds)
                if isinstance(creds, dict) and "_encrypted" in creds:
                    from core.crypto import decrypt_for_tenant

                    creds = _cjson.loads(decrypt_for_tenant(creds["_encrypted"]))
                config = {**(cc.config or {}), **(creds or {})}
    except Exception:
        _log.debug("connector_test_encrypted_creds_load_failed conn_id=%s", conn_id)
    # TC_006 (Aishwarya 2026-04-23): CA-firms store GSTN credentials
    # in the per-company Credential Vault (GSTNCredential) via
    # /companies/{id}/credentials. That vault exists for multi-company
    # firms and shows "Verified" in the UI. Historically the connector
    # test only looked in ConnectorConfig.credentials_encrypted and
    # ignored the vault, so testers saw "test failed" even though the
    # vault was clearly populated. Bridge the two stores for the GSTN
    # connector: if ConnectorConfig has no creds, try the active
    # GSTNCredential for this tenant.
    if not config and connector.name.lower() in ("gstn", "gst"):
        try:
            from core.crypto import decrypt_for_tenant
            from core.models.gstn_credential import GSTNCredential

            async with get_tenant_session(tid) as session:
                gstn_result = await session.execute(
                    select(GSTNCredential)
                    .where(
                        GSTNCredential.tenant_id == tid,
                        GSTNCredential.is_active.is_(True),
                    )
                    .order_by(GSTNCredential.last_verified_at.desc())
                    .limit(1)
                )
                gstn_cred = gstn_result.scalar_one_or_none()
            if gstn_cred and gstn_cred.password_encrypted:
                decrypted_pw = decrypt_for_tenant(gstn_cred.password_encrypted)
                config = {
                    "gstin": gstn_cred.gstin,
                    "username": gstn_cred.username,
                    "password": decrypted_pw,
                    "portal_type": gstn_cred.portal_type or "gstn",
                }
        except Exception:
            _log.debug("connector_test_gstn_vault_bridge_failed conn_id=%s", conn_id)

    if not config:
        # Codex 2026-04-23 re-verification blocker G: runtime execution
        # (core/tool_gateway/gateway.py) refuses to fall back to the
        # plaintext Connector.auth_config. Historically this endpoint
        # DID fall back, which gave attackers a parity gap — credentials
        # that the runtime wouldn't accept could still be exercised via
        # the test endpoint. Close the gap: refuse the test when no
        # encrypted credentials are present. Admins must re-register
        # the connector through the create/update path, which writes
        # to connector_configs.credentials_encrypted via encrypt_for_tenant.
        hint = (
            "Connector has no encrypted credentials. Re-register the "
            "connector via POST/PUT /connectors so credentials land "
            "in the encrypted vault (connector_configs.credentials_"
            "encrypted). Plaintext auth_config is no longer accepted "
            "by either runtime execution or this test path."
        )
        if connector.name.lower() in ("gstn", "gst"):
            hint += (
                " For GSTN: if you used the per-company Credential Vault "
                "(Company → Settings → GSTN Credentials), ensure the "
                "credential is marked Active — the connector test "
                "automatically uses the most recent active vault entry."
            )
        return {
            "tested": False,
            "name": connector.name,
            "error": hint,
        }
    try:
        instance = connector_cls(config)
        await asyncio.wait_for(instance.connect(), timeout=10)
        health = await asyncio.wait_for(instance.health_check(), timeout=10)
        await instance.disconnect()

        # Update last health check time
        from datetime import UTC, datetime

        async with get_tenant_session(tid) as session:
            result = await session.execute(
                select(Connector).where(Connector.id == conn_id)
            )
            db_conn = result.scalar_one_or_none()
            if db_conn:
                db_conn.health_check_at = datetime.now(UTC)
                db_conn.status = "active" if health.get("status") == "healthy" else "error"

        return {
            "tested": True,
            "name": connector.name,
            "health": health,
        }
    except TimeoutError:
        # TC_007 (Aishwarya 2026-04-23): "Connection timed out (10s)" is
        # specific; return a hint about what to check.
        return {
            "tested": False,
            "name": connector.name,
            "error": (
                "Connection timed out after 10s. The connector's base URL "
                "did not respond — check that the endpoint is reachable "
                "from the platform network and that no upstream proxy is "
                "blocking outbound traffic."
            ),
        }
    except Exception as exc:
        # TC_007 (Aishwarya 2026-04-23): the route used to always return
        # "Connector test failed" regardless of the actual cause (401,
        # SSL, DNS, auth token expired, etc.). Surface a specific,
        # actionable message derived from the exception so operators can
        # debug without tailing server logs. Still suppresses secrets by
        # never echoing the raw request payload.
        _log.exception("connector_test_failed conn_id=%s name=%s", conn_id, connector.name)
        err_name = type(exc).__name__
        err_msg = str(exc)[:240]
        # Map common failure classes to actionable hints.
        lowered = (err_name + " " + err_msg).lower()
        if "401" in lowered or "unauthor" in lowered or "invalid credentials" in lowered:
            hint = "Invalid credentials — verify username/password or API key in the credential vault."
        elif "403" in lowered or "forbidden" in lowered:
            hint = "Authentication succeeded but the account is not authorized for this resource."
        elif "ssl" in lowered or "certificate" in lowered:
            hint = "SSL / certificate validation failed. Check base URL protocol and peer certificate."
        elif "dns" in lowered or "name resolution" in lowered or "nodename" in lowered:
            hint = "DNS lookup failed for the connector base URL."
        elif "connectionrefused" in lowered or "connection refused" in lowered:
            hint = "Target refused the connection — host is up but the port is not accepting traffic."
        elif "token" in lowered and ("expired" in lowered or "refresh" in lowered):
            hint = "Authentication token expired — refresh the OAuth grant or re-authorize."
        elif "timeout" in lowered:
            hint = "Request timed out — endpoint is slow or unreachable from the platform."
        else:
            hint = f"{err_name}: {err_msg}" if err_msg else f"{err_name} (no detail)"
        return {
            "tested": False,
            "name": connector.name,
            "error": hint,
            "error_class": err_name,
        }

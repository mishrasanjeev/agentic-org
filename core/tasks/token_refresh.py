"""Celery task: refresh OAuth tokens before expiry.

Runs every 15 minutes via Celery Beat. For each connector_config that
has a refresh_token and an expires_at within the next 30 minutes:
  1. Call the token endpoint with grant_type=refresh_token
  2. Encrypt the new access_token + refresh_token
  3. Update connector_configs.credentials_encrypted
  4. Log the refresh event

If the refresh fails, log a warning — the connector will fail at
execution time and the user can manually re-authenticate.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
import structlog

from core.tasks.celery_app import app

logger = structlog.get_logger()


@app.task(name="core.tasks.token_refresh.refresh_expiring_tokens")
def refresh_expiring_tokens() -> dict:
    """Celery-compatible sync entry point."""
    return asyncio.run(_refresh_all())


async def _refresh_all() -> dict:
    from sqlalchemy import select

    from core.database import async_session_factory
    from core.models.connector_config import ConnectorConfig

    refreshed = 0
    failed = 0
    skipped = 0
    threshold = datetime.now(UTC) + timedelta(minutes=30)

    async with async_session_factory() as session:
        result = await session.execute(select(ConnectorConfig))
        configs = result.scalars().all()

    for cc in configs:
        try:
            creds = cc.credentials_encrypted
            if isinstance(creds, str):
                creds = json.loads(creds)
            if not isinstance(creds, dict):
                skipped += 1
                continue

            # Unwrap if envelope-encrypted
            if "_encrypted" in creds:
                from core.crypto import decrypt_for_tenant

                try:
                    creds = json.loads(decrypt_for_tenant(creds["_encrypted"]))
                except Exception:
                    skipped += 1
                    continue

            refresh_token = creds.get("refresh_token", "")
            token_url = creds.get("token_url", "")
            client_id = creds.get("client_id", "")
            client_secret = creds.get("client_secret", "")
            expires_at_str = creds.get("expires_at", "")

            if not refresh_token or not token_url:
                skipped += 1
                continue

            # Check if token is expiring soon
            if expires_at_str:
                try:
                    exp = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                    if exp > threshold:
                        skipped += 1  # not expiring yet
                        continue
                except (ValueError, TypeError):
                    pass  # can't parse — try to refresh anyway

            # Refresh the token
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(
                    token_url,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                )
                resp.raise_for_status()
                token_data = resp.json()

            new_access = token_data.get("access_token", "")
            new_refresh = token_data.get("refresh_token", refresh_token)
            new_expires_in = token_data.get("expires_in", 3600)
            new_expires_at = (
                datetime.now(UTC) + timedelta(seconds=int(new_expires_in))
            ).isoformat()

            if not new_access:
                failed += 1
                continue

            # Update credentials
            creds["access_token"] = new_access
            creds["refresh_token"] = new_refresh
            creds["expires_at"] = new_expires_at

            # Re-encrypt and store
            from core.crypto import encrypt_for_tenant

            encrypted = await encrypt_for_tenant(json.dumps(creds), cc.tenant_id)

            async with async_session_factory() as write_session:
                from sqlalchemy import select as _sel

                row = await write_session.execute(
                    _sel(ConnectorConfig).where(ConnectorConfig.id == cc.id)
                )
                fresh = row.scalar_one_or_none()
                if fresh:
                    fresh.credentials_encrypted = {"_encrypted": encrypted}
                    await write_session.commit()

            refreshed += 1
            logger.info(
                "token_refreshed",
                connector=cc.connector_name,
                tenant_id=str(cc.tenant_id),
                new_expires_at=new_expires_at,
            )
        except Exception as exc:
            failed += 1
            logger.warning(
                "token_refresh_failed",
                connector=cc.connector_name,
                tenant_id=str(cc.tenant_id),
                error=str(exc)[:200],
            )

    summary = {"refreshed": refreshed, "failed": failed, "skipped": skipped}
    logger.info("token_refresh_run", **summary)
    return summary

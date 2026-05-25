#!/usr/bin/env python3
"""Configure CMO weekly-report vendor-sandbox ConnectorConfig rows.

This is a local/QA helper. It never invents credentials and never logs raw
credential values. Credentials can be supplied either through the existing
`AGENTICORG_CMO_SANDBOX_*` env vars used by the runner, or through a gitignored
JSON file such as `secrets/cmo_vendor_sandbox_connectors.json`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.marketing.weekly_report_sandbox_pilot import (  # noqa: E402
    SANDBOX_CONNECTOR_OPTIONS,
    SANDBOX_ENV_PREFIX,
)

CATEGORY_ORDER = ("CRM", "Ads", "Analytics", "Email")
DEFAULT_CONFIG_PATH = REPO_ROOT / "secrets" / "cmo_vendor_sandbox_connectors.json"
SECRET_KEY_MARKERS = ("secret", "token", "password", "credential", "api_key", "authorization")


@dataclass(frozen=True)
class ConnectorInput:
    category: str
    connector_name: str
    display_name: str
    auth_type: str
    credentials: dict[str, Any]
    non_secret_config: dict[str, Any]
    source: str


def _load_local_dotenv() -> None:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Gitignored JSON file containing QA-provided sandbox connector credentials.",
    )
    parser.add_argument(
        "--tenant-id",
        default=None,
        help=f"Tenant UUID. Defaults to {SANDBOX_ENV_PREFIX}TENANT_ID.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate only; do not write DB rows.")
    parser.add_argument("--format", choices=("text", "json"), default="text")
    return parser.parse_args()


def _load_file_inputs(path: Path) -> dict[str, ConnectorInput]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    connectors = payload.get("connectors", payload)
    if not isinstance(connectors, Mapping):
        raise ValueError("Connector config file must contain a connectors object.")

    loaded: dict[str, ConnectorInput] = {}
    for category in CATEGORY_ORDER:
        row = connectors.get(category) or connectors.get(category.lower())
        if not isinstance(row, Mapping):
            continue
        credentials = row.get("credentials")
        if not isinstance(credentials, Mapping) or not credentials:
            continue
        connector_name = str(row.get("connector_name") or row.get("provider") or "").strip()
        if not connector_name:
            raise ValueError(f"{category} config is missing connector_name/provider.")
        loaded[category] = ConnectorInput(
            category=category,
            connector_name=connector_name,
            display_name=str(row.get("display_name") or connector_name),
            auth_type=str(row.get("auth_type") or "oauth2"),
            credentials=dict(credentials),
            non_secret_config=_safe_non_secret_config(row.get("config")),
            source=f"file:{path.name}",
        )
    return loaded


def _load_env_inputs(env: Mapping[str, str]) -> dict[str, ConnectorInput]:
    loaded: dict[str, ConnectorInput] = {}
    for category, options in SANDBOX_CONNECTOR_OPTIONS.items():
        for option in options:
            missing = [name for name in option["required_envs"] if not env.get(name)]
            if missing:
                continue
            credential_env_map = option["credential_env_map"]
            credentials = {
                key: str(env[name])
                for key, name in credential_env_map.items()
                if env.get(name) is not None
            }
            loaded[category] = ConnectorInput(
                category=category,
                connector_name=str(option["connector_key"]),
                display_name=str(option["connector_key"]).replace("_", " ").title(),
                auth_type="oauth2",
                credentials=credentials,
                non_secret_config={},
                source="env",
            )
            break
    return loaded


async def _upsert_connector_configs(
    tenant_id: uuid.UUID,
    inputs: Mapping[str, ConnectorInput],
    *,
    dry_run: bool,
) -> dict[str, Any]:
    from sqlalchemy import select

    from core.crypto import encrypt_for_tenant
    from core.database import async_session_factory
    from core.models.connector_config import ConnectorConfig
    from core.models.tenant import Tenant

    async with async_session_factory() as session:
        tenant_result = await session.execute(select(Tenant.id).where(Tenant.id == tenant_id))
        if tenant_result.scalar_one_or_none() is None:
            return {
                "status": "blocked",
                "message": "Target tenant does not exist.",
                "categories": [],
                "missing_categories": list(CATEGORY_ORDER),
            }

        summaries: list[dict[str, Any]] = []
        for category in CATEGORY_ORDER:
            connector_input = inputs[category]
            config = _vendor_sandbox_config(connector_input)
            encrypted = await encrypt_for_tenant(
                json.dumps(connector_input.credentials, sort_keys=True),
                tenant_id,
            )
            existing_result = await session.execute(
                select(ConnectorConfig).where(
                    ConnectorConfig.tenant_id == tenant_id,
                    ConnectorConfig.connector_name == connector_input.connector_name,
                )
            )
            existing = existing_result.scalar_one_or_none()
            action = "would_insert" if dry_run else "inserted"
            if existing is not None:
                action = "would_update" if dry_run else "updated"
                if not _replacement_has_real_sandbox_metadata(config, connector_input.credentials):
                    return {
                        "status": "blocked",
                        "message": f"Refusing to overwrite {category}; replacement is not real vendor-sandbox metadata.",
                        "categories": summaries,
                        "missing_categories": [],
                    }
                if not dry_run:
                    existing.display_name = connector_input.display_name
                    existing.auth_type = connector_input.auth_type
                    existing.credentials_encrypted = {"_encrypted": encrypted}
                    existing.config = config
                    existing.status = "configured"
                    existing.health_status = "healthy"
                    existing.sync_error = None
            elif not dry_run:
                session.add(
                    ConnectorConfig(
                        tenant_id=tenant_id,
                        connector_name=connector_input.connector_name,
                        display_name=connector_input.display_name,
                        auth_type=connector_input.auth_type,
                        credentials_encrypted={"_encrypted": encrypted},
                        config=config,
                        status="configured",
                        health_status="healthy",
                    )
                )
            summaries.append(
                {
                    "category": category,
                    "connector_name": connector_input.connector_name,
                    "source": connector_input.source,
                    "action": action,
                    "credential_keys_present": sorted(connector_input.credentials),
                    "proof_scope": "vendor_sandbox",
                    "local_test_only": False,
                    "mock_or_test_double": False,
                }
            )
        if not dry_run:
            await session.commit()
        return {
            "status": "ready",
            "message": "Vendor-sandbox ConnectorConfig rows validated." if dry_run else "Vendor-sandbox ConnectorConfig rows configured.",
            "categories": summaries,
            "missing_categories": [],
        }


def _vendor_sandbox_config(connector_input: ConnectorInput) -> dict[str, Any]:
    safe_config = {
        **connector_input.non_secret_config,
        "cmo_category": connector_input.category,
        "proof_scope": "vendor_sandbox",
        "environment_type": "vendor_sandbox",
        "local_test_only": False,
        "mock_or_test_double": False,
        "sandbox_preflight_ready": True,
        "configured_by": "scripts/configure_cmo_vendor_sandbox_connectors.py",
        "configured_at": datetime.now(UTC).isoformat(),
    }
    return _strip_secret_like_keys(safe_config)


def _replacement_has_real_sandbox_metadata(config: Mapping[str, Any], credentials: Mapping[str, Any]) -> bool:
    return (
        bool(credentials)
        and config.get("proof_scope") == "vendor_sandbox"
        and config.get("environment_type") == "vendor_sandbox"
        and config.get("local_test_only") is False
        and config.get("mock_or_test_double") is False
    )


def _safe_non_secret_config(value: Any) -> dict[str, Any]:
    return _strip_secret_like_keys(value if isinstance(value, dict) else {})


def _strip_secret_like_keys(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        str(key): item
        for key, item in value.items()
        if not _is_secret_key(str(key)) and not isinstance(item, Mapping)
    }


def _is_secret_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(marker in normalized for marker in SECRET_KEY_MARKERS)


def _safe_summary(summary: Mapping[str, Any]) -> dict[str, Any]:
    redacted = json.loads(json.dumps(summary, default=str))
    for category in redacted.get("categories", []):
        if isinstance(category, dict) and "credential_keys_present" in category:
            category["credential_values_redacted"] = True
    return redacted


async def _amain() -> int:
    _load_local_dotenv()
    args = _parse_args()
    tenant_id_raw = args.tenant_id or os.getenv(f"{SANDBOX_ENV_PREFIX}TENANT_ID")
    if not tenant_id_raw:
        summary = {
            "status": "blocked",
            "message": f"Missing {SANDBOX_ENV_PREFIX}TENANT_ID.",
            "missing_categories": list(CATEGORY_ORDER),
        }
    else:
        try:
            tenant_id = uuid.UUID(str(tenant_id_raw))
        except ValueError:
            summary = {
                "status": "blocked",
                "message": f"{SANDBOX_ENV_PREFIX}TENANT_ID is not a valid UUID.",
                "missing_categories": list(CATEGORY_ORDER),
            }
        else:
            file_inputs = _load_file_inputs(Path(args.config))
            env_inputs = _load_env_inputs(os.environ)
            inputs = {**env_inputs, **file_inputs}
            missing = [category for category in CATEGORY_ORDER if category not in inputs]
            if missing:
                summary = {
                    "status": "blocked",
                    "message": "Missing real vendor-sandbox credentials for one or more categories.",
                    "missing_categories": missing,
                    "configured_categories": sorted(inputs),
                    "config_file_checked": str(Path(args.config)),
                }
            else:
                summary = await _upsert_connector_configs(
                    tenant_id,
                    inputs,
                    dry_run=args.dry_run,
                )

    safe = _safe_summary(summary)
    if args.format == "json":
        print(json.dumps(safe, indent=2, sort_keys=True))
    else:
        print(f"Status: {safe.get('status')}")
        print(str(safe.get("message") or ""))
        for category in safe.get("categories", []):
            print(
                f"- {category['category']}: {category['connector_name']} "
                f"({category['action']}, source={category['source']})"
            )
        missing = safe.get("missing_categories") or []
        if missing:
            print("Missing categories: " + ", ".join(missing))
    return 0 if safe.get("status") == "ready" else 3


def main() -> int:
    return asyncio.run(_amain())


if __name__ == "__main__":
    raise SystemExit(main())

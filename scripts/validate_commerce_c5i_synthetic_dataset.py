"""Validate the C5I synthetic commerce merchant dataset."""

from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DATASET_PATH = ROOT / "docs" / "examples" / "commerce-agent-c5i-synthetic-merchant.dataset.json"
GUIDE_PATH = ROOT / "docs" / "commerce-agent-c5i-synthetic-merchant-dataset.md"
DISCOVERY_GATE_PATH = ROOT / "core" / "commerce" / "discovery_gate.py"


def _joined(*parts: str) -> str:
    return "".join(parts)

SAFE_FALSE_CREDENTIAL_KEYS = {
    "credential_material_included",
    "provider_credentials_included",
}

FALSE_ONLY_KEYS = {
    "agenticorg_commerce_public_discovery_enabled",
    "agenticorg_public_discovery_approval",
    "agenticorg_public_discovery_enabled",
    "auth_material_included",
    "checkout_creation_allowed",
    "checkout_payment_creation_allowed",
    "checkout_payment_creation_enabled_by_discovery_gate",
    "direct_pine_allowed",
    "direct_plural_allowed",
    "direct_provider_tools_allowed",
    "direct_stripe_allowed",
    "live_payments_enabled",
    "live_plural_enabled",
    "payment_creation_allowed",
    "plural_live_enabled",
    "production_approval",
    "production_config_value_allowed",
    "production_discovery_approval",
    "production_url_allowed",
    "public_discovery_enabled",
    "resource_creation_allowed",
    "sensitive_values_allowed_in_git",
    "values_included",
}

TRUE_ONLY_KEYS = {
    "base_url_is_placeholder",
    "commerce_agent_hidden_by_default",
    "internal_only",
    "path_required_under_tmp",
    "synthetic_only",
}

SECRET_KEY_FRAGMENTS = (
    "api_key",
    "bearer",
    "client_secret",
    "credential_payload",
    "credential_ref",
    "password",
    "private_key",
    "secret",
    "token",
    "webhook_secret",
)

SECRET_VALUE_PATTERNS = (
    re.compile(_joined("Bearer", r"\s+[A-Za-z0-9._-]{8,}")),
    re.compile(_joined("sk", "_live_"), re.IGNORECASE),
    re.compile(_joined("pk", "_live_"), re.IGNORECASE),
    re.compile(_joined("grtx", "_live_"), re.IGNORECASE),
    re.compile(_joined("-----", "BEGIN"), re.IGNORECASE),
    re.compile(_joined("passport", r"\.jwt"), re.IGNORECASE),
    re.compile(_joined("idempotency", "-key:"), re.IGNORECASE),
    re.compile(_joined("mock", "-webhook", "-secret"), re.IGNORECASE),
    re.compile(_joined("postgres", "://"), re.IGNORECASE),
    re.compile(_joined("redis", "://"), re.IGNORECASE),
)

OVERCLAIM_PATTERNS = (
    re.compile(_joined("approved for ", "production"), re.IGNORECASE),
    re.compile(_joined("certification ", "complete"), re.IGNORECASE),
    re.compile(_joined("certified ", "merchant"), re.IGNORECASE),
    re.compile(_joined("checkout creation ", "enabled"), re.IGNORECASE),
    re.compile(_joined("live payments ", "enabled"), re.IGNORECASE),
    re.compile(_joined("live plural ", "enabled"), re.IGNORECASE),
    re.compile(_joined("payment creation ", "enabled"), re.IGNORECASE),
    re.compile(_joined("production ", "approved"), re.IGNORECASE),
    re.compile(_joined("production ", "ready"), re.IGNORECASE),
    re.compile(_joined("public discovery ", "enabled"), re.IGNORECASE),
    re.compile(_joined("ready for ", "production"), re.IGNORECASE),
)

REALISTIC_NAME_PATTERNS = (
    re.compile(r"\bacme\b", re.IGNORECASE),
    re.compile(r"\bamazon\b", re.IGNORECASE),
    re.compile(r"\bapple\b", re.IGNORECASE),
    re.compile(r"\bcroma\b", re.IGNORECASE),
    re.compile(r"\bflipkart\b", re.IGNORECASE),
    re.compile(r"\breliance\b", re.IGNORECASE),
    re.compile(r"\bsamsung\b", re.IGNORECASE),
    re.compile(r"\btata\b", re.IGNORECASE),
    re.compile(r"\bvijay sales\b", re.IGNORECASE),
    re.compile(r"\bpinelabs\b", re.IGNORECASE),
    re.compile(r"\bplural\b", re.IGNORECASE),
)


class SyntheticDatasetValidationError(ValueError):
    """Raised when the C5I synthetic dataset is not safe for internal smoke use."""


def _fail(errors: list[str], path: str, message: str) -> None:
    errors.append(f"{path}: {message}")


def _assert_synthetic_id(value: Any, path: str, prefix: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        _fail(errors, path, "must be a string")
        return
    if not value.startswith(prefix):
        _fail(errors, path, f"must start with {prefix}")
    if "synth" not in value or "internal" not in value or "smoke" not in value:
        _fail(errors, path, "must include synth, internal, and smoke markers")
    normalized = value.replace("_", " ")
    if re.search(r"\b(prod|production|live|real|public)\b", normalized, re.IGNORECASE):
        _fail(errors, path, "must not contain production/live/real/public markers")


def _assert_synthetic_name(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str):
        _fail(errors, path, "must be a string")
        return
    lowered = value.lower()
    if "synthetic" not in lowered or ("internal" not in lowered and "smoke" not in lowered):
        _fail(errors, path, "must be visibly synthetic/internal/smoke")
    for pattern in REALISTIC_NAME_PATTERNS:
        if pattern.search(value):
            _fail(errors, path, "must not use a real or realistic merchant/brand name")


def _inspect_value(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            nested_path = f"{path}.{key}"
            lowered = key.lower()
            if lowered in SAFE_FALSE_CREDENTIAL_KEYS:
                if nested is not False:
                    _fail(errors, nested_path, "credential inclusion flags must be false")
            elif any(fragment in lowered for fragment in SECRET_KEY_FRAGMENTS):
                _fail(errors, nested_path, "secret or provider credential keys are not allowed")
            if lowered in FALSE_ONLY_KEYS and nested is not False:
                _fail(errors, nested_path, "must be false")
            if lowered in TRUE_ONLY_KEYS and nested is not True:
                _fail(errors, nested_path, "must be true")
            _inspect_value(nested, nested_path, errors)
        return

    if isinstance(value, list):
        for index, item in enumerate(value):
            _inspect_value(item, f"{path}[{index}]", errors)
        return

    if not isinstance(value, str):
        return

    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.search(value):
            _fail(errors, path, "contains secret-like material")
    for pattern in OVERCLAIM_PATTERNS:
        if pattern.search(value):
            _fail(errors, path, "contains a production/live/readiness overclaim")


def validate_dataset(dataset: dict[str, Any]) -> None:
    errors: list[str] = []

    if dataset.get("dataset_version") != "c5i-synth-v1":
        _fail(errors, "dataset_version", "must be c5i-synth-v1")
    if dataset.get("synthetic_only") is not True:
        _fail(errors, "synthetic_only", "must be true")
    if dataset.get("internal_only") is not True:
        _fail(errors, "internal_only", "must be true")
    if dataset.get("provider_boundary", {}).get("provider_key") != "mock":
        _fail(errors, "provider_boundary.provider_key", "must be mock")
    if dataset.get("merchant", {}).get("certification_claim") != "none":
        _fail(errors, "merchant.certification_claim", "must be none")
    if dataset.get("merchant", {}).get("readiness_claim") != "none":
        _fail(errors, "merchant.readiness_claim", "must be none")
    if dataset.get("public_discovery", {}).get("certification_claim") != "none":
        _fail(errors, "public_discovery.certification_claim", "must be none")
    if dataset.get("public_discovery", {}).get("readiness_claim") != "none":
        _fail(errors, "public_discovery.readiness_claim", "must be none")

    _assert_synthetic_id(dataset.get("merchant", {}).get("id"), "merchant.id", "mch_synth_internal_smoke_", errors)
    _assert_synthetic_id(dataset.get("agent", {}).get("id"), "agent.id", "cag_synth_internal_smoke_", errors)
    _assert_synthetic_id(
        dataset.get("catalog_refs", {}).get("product_id"),
        "catalog_refs.product_id",
        "cprd_synth_internal_smoke_",
        errors,
    )
    _assert_synthetic_id(
        dataset.get("catalog_refs", {}).get("variant_id"),
        "catalog_refs.variant_id",
        "cvar_synth_internal_smoke_",
        errors,
    )

    _assert_synthetic_name(dataset.get("merchant", {}).get("display_name"), "merchant.display_name", errors)
    _assert_synthetic_name(dataset.get("merchant", {}).get("public_name"), "merchant.public_name", errors)
    _assert_synthetic_name(dataset.get("agent", {}).get("display_name"), "agent.display_name", errors)

    _inspect_value(dataset, "dataset", errors)

    if errors:
        raise SyntheticDatasetValidationError("C5I synthetic dataset validation failed:\n" + "\n".join(errors))


def validate_docs_and_gate() -> None:
    guide = GUIDE_PATH.read_text(encoding="utf-8")
    discovery_gate = DISCOVERY_GATE_PATH.read_text(encoding="utf-8")

    required = (
        "does not deploy",
        "does not approve or authorize",
        "Production discovery",
        "AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED",
        "Grantex public discovery flags or merchant allowlist values",
        "Any production config value",
        "Checkout creation",
        "Payment intent creation",
        "Live payments",
        "Live Plural",
        "Certification and readiness claims remain `none`",
        "AgenticOrg commerce discovery remains hidden by default",
    )
    for phrase in required:
        if phrase not in guide:
            raise SyntheticDatasetValidationError(f"guide missing required phrase: {phrase}")

    forbidden = (
        _joined("AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED", "=true"),
        _joined("COMMERCE_PUBLIC_DISCOVERY_ENABLED", "=true"),
        _joined("COMMERCE_PUBLIC_DISCOVERY_MERCHANT_ALLOWLIST", "=mch_"),
        _joined("COMMERCE_LIVE_MODE_ENABLED", "=true"),
        _joined("PLURAL_LIVE_ENABLED", "=true"),
        _joined("Bearer", " "),
        _joined("sk", "_live_"),
        _joined("pk", "_live_"),
        _joined("-----", "BEGIN"),
    )
    for phrase in forbidden:
        if phrase in guide:
            raise SyntheticDatasetValidationError(f"guide includes forbidden phrase: {phrase}")

    if 'COMMERCE_PUBLIC_DISCOVERY_ENV = "AGENTICORG_COMMERCE_PUBLIC_DISCOVERY_ENABLED"' not in discovery_gate:
        raise SyntheticDatasetValidationError("AgenticOrg discovery gate env name changed unexpectedly")
    if 'return value.strip().lower() in _TRUE_VALUES' not in discovery_gate:
        raise SyntheticDatasetValidationError("AgenticOrg discovery gate is not explicit true-value only")
    if "continue" not in discovery_gate or "not commerce_enabled" not in discovery_gate:
        raise SyntheticDatasetValidationError("AgenticOrg commerce discovery is not hidden by default")


def _assert_rejects(base_dataset: dict[str, Any], label: str, mutate: Any) -> None:
    candidate = copy.deepcopy(base_dataset)
    mutate(candidate)
    try:
        validate_dataset(candidate)
    except SyntheticDatasetValidationError:
        return
    raise AssertionError(f"expected validator to reject {label}")


def run_negative_cases(dataset: dict[str, Any]) -> None:
    _assert_rejects(
        dataset,
        "production-looking merchant ID",
        lambda candidate: candidate["merchant"].update(id=_joined("mch_pr", "od", "_ready_merchant_001")),
    )
    _assert_rejects(
        dataset,
        "realistic merchant name",
        lambda candidate: candidate["merchant"].update(display_name="Acme Retail Private Limited"),
    )
    _assert_rejects(
        dataset,
        "secret-like value",
        lambda candidate: candidate["provider_boundary"].update(note=_joined("Bearer", " fixture-token-value")),
    )
    _assert_rejects(
        dataset,
        "provider credential key",
        lambda candidate: candidate["provider_boundary"].update(credential_payload={"value": "synthetic"}),
    )
    _assert_rejects(
        dataset,
        "live payment flag",
        lambda candidate: candidate["provider_boundary"].update(live_payments_enabled=True),
    )
    _assert_rejects(
        dataset,
        "live payment claim",
        lambda candidate: candidate.update(purpose=_joined("Synthetic dataset with live payments", " enabled")),
    )
    _assert_rejects(
        dataset,
        "certification overclaim",
        lambda candidate: candidate["merchant"].update(certification_claim=_joined("certified", " merchant")),
    )
    _assert_rejects(
        dataset,
        "readiness overclaim",
        lambda candidate: candidate["merchant"].update(readiness_claim=_joined("production", " ready")),
    )


def main() -> None:
    dataset_text = DATASET_PATH.read_text(encoding="utf-8")
    if any(ord(char) > 127 for char in dataset_text):
        raise SyntheticDatasetValidationError("dataset must stay ASCII-only")
    dataset = json.loads(dataset_text)
    validate_dataset(dataset)
    validate_docs_and_gate()
    run_negative_cases(dataset)
    print("commerce C5I synthetic AgenticOrg dataset validation passed")


if __name__ == "__main__":
    main()

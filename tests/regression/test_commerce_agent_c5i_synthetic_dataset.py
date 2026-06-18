from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from scripts.validate_commerce_c5i_synthetic_dataset import (
    DATASET_PATH,
    SyntheticDatasetValidationError,
    _joined,
    validate_dataset,
    validate_docs_and_gate,
)


def _dataset() -> dict:
    return json.loads(Path(DATASET_PATH).read_text(encoding="utf-8"))


def test_c5i_synthetic_dataset_is_valid_and_gate_remains_default_hidden() -> None:
    validate_dataset(_dataset())
    validate_docs_and_gate()


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        (
            "production-looking merchant ID",
            lambda data: data["merchant"].update(id=_joined("mch_pr", "od", "_ready_merchant_001")),
        ),
        ("realistic merchant name", lambda data: data["merchant"].update(display_name="Acme Retail Private Limited")),
        (
            "secret-like value",
            lambda data: data["provider_boundary"].update(note=_joined("Bearer", " fixture-token-value")),
        ),
        (
            "provider credential key",
            lambda data: data["provider_boundary"].update(credential_payload={"value": "synthetic"}),
        ),
        ("live payment flag", lambda data: data["provider_boundary"].update(live_payments_enabled=True)),
        (
            "live payment claim",
            lambda data: data.update(purpose=_joined("Synthetic dataset with live payments", " enabled")),
        ),
        (
            "certification overclaim",
            lambda data: data["merchant"].update(certification_claim=_joined("certified", " merchant")),
        ),
        (
            "readiness overclaim",
            lambda data: data["merchant"].update(readiness_claim=_joined("production", " ready")),
        ),
    ],
)
def test_c5i_synthetic_dataset_rejects_unsafe_drift(label: str, mutate) -> None:
    candidate = copy.deepcopy(_dataset())
    mutate(candidate)

    with pytest.raises(SyntheticDatasetValidationError):
        validate_dataset(candidate)

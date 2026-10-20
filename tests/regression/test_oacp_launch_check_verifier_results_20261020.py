# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""Regression: the OACP launch check reports verifier results, not artifact counts (G5).

Failure replayed: ``scripts/oacp_runtime_launch_check.py`` built eleven
unsigned local artifacts, stamped each ``verifier_status.valid = True`` and
wrote ``artifact_verifier_summary.valid = len(artifacts)`` into the launch
evidence, so the evidence claimed eleven verified artifacts while the runtime
cached all eleven unverified (one ``oacp_cache_record_unverified_irregularity``
per record). A tampered artifact still counted as valid.

Now the run signs the local artifacts with a throwaway ES256 key, runs each one
through ``OacpGrantexArtifactVerifier`` (detached JWS, JWKS key, scope, TTL,
Token Status List) and counts what the verifier returned.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from structlog.testing import capture_logs

from scripts import oacp_runtime_launch_check as launch_check


def _run_launch_check(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    monkeypatch.setattr(launch_check, "WRITE_EVIDENCE", False)
    monkeypatch.setattr(launch_check, "EXTERNAL_CHECKS_ENABLED", False)
    asyncio.run(launch_check.main())
    return json.loads(capsys.readouterr().out)


def test_launch_check_counts_verified_artifacts(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    with capture_logs() as logs:
        summary = _run_launch_check(monkeypatch, capsys)

    verifier = summary["artifact_verifier_summary"]
    assert verifier["checked"] == summary["local_artifact_family_count"] == 11
    assert verifier["valid"] == 11
    assert verifier["invalid"] == 0
    assert verifier["status_list_checked"] == 11
    assert verifier["verification_mode"] == "local_throwaway_es256_key"
    assert verifier["status_list_source"] == "local_in_process_status_list_all_valid"
    assert verifier["grantex_issuer"] is False
    assert summary["cache_records_verified_count"] == 11
    assert not [entry for entry in logs if entry.get("event") == "oacp_cache_record_unverified_irregularity"]


def test_launch_check_counts_a_tampered_artifact_as_invalid(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    original = launch_check._local_artifacts

    def tampered(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        artifacts = original(*args, **kwargs)
        # Changed after signing: the payload no longer matches payload_hash.
        artifacts[0]["payload"]["source_observed_at"] = "2020-01-01T00:00:00Z"
        return artifacts

    monkeypatch.setattr(launch_check, "_local_artifacts", tampered)
    monkeypatch.setattr(launch_check, "WRITE_EVIDENCE", False)
    monkeypatch.setattr(launch_check, "EXTERNAL_CHECKS_ENABLED", False)
    with capture_logs() as logs, pytest.raises(SystemExit) as exit_info:  # logs kept off stdout
        asyncio.run(launch_check.main())
    assert "refused 1 local artifact" in str(exit_info.value)
    summary = json.loads(capsys.readouterr().out)

    verifier = summary["artifact_verifier_summary"]
    assert verifier["valid"] == 10
    assert verifier["invalid"] == 1
    assert verifier["refusal_codes"] == ["payload_hash_mismatch"]
    assert summary["cache_records_verified_count"] == 10
    unverified = [entry for entry in logs if entry.get("event") == "oacp_cache_record_unverified_irregularity"]
    assert unverified and {entry["refusal_code"] for entry in unverified} == {"artifact_unverified"}


def test_summary_is_taken_from_results_not_counts() -> None:
    results = [
        {"valid": True, "status_checked": True},
        {"valid": False, "refusal_code": "issuer_key_untrusted"},
        {"valid": False, "refusal_code": "artifact_revoked"},
    ]
    summary = launch_check.summarize_verifier_results(results)
    assert (summary["checked"], summary["valid"], summary["invalid"], summary["status_list_checked"]) == (3, 1, 2, 1)
    assert summary["refusal_codes"] == ["artifact_revoked", "issuer_key_untrusted"]


def test_launch_check_key_is_generated_per_run() -> None:
    first, second = launch_check.LaunchCheckSigner(), launch_check.LaunchCheckSigner()
    assert first.kid.startswith("oacp-launchcheck-")
    assert first.kid != second.kid
    assert first.public_jwk()["x"] != second.public_jwk()["x"]
    assert "d" not in first.public_jwk()

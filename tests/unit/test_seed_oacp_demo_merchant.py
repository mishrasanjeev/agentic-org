# SPDX-License-Identifier: Apache-2.0
"""scripts/seed_oacp_demo_merchant.py: refusals before any write, argument rules and Grantex stops.

The database paths (create, converge, teardown) run on Postgres in
tests/integration/test_oacp_demo_merchant_postgres.py.
"""

from __future__ import annotations

import json

import httpx
import pytest

from core.commerce.oacp_demo_merchant import DEMO_TENANT_ID
from core.config import settings
from scripts import seed_oacp_demo_merchant as seed

READY_ENV = {
    "AGENTICORG_DB_URL": "postgresql+asyncpg://agenticorg:placeholder@127.0.0.1:5432/agenticorg",
    "GRANTEX_COMMERCE_BASE_URL": "https://grantex.example",
    "GRANTEX_COMMERCE_INTERNAL_TOKEN": "placeholder-service-token",
}


@pytest.fixture()
def demo_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "oacp_demo_merchant_enabled", True)


def _no_database(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _refuse(*_args, **_kwargs):
        raise AssertionError("the run must stop before opening the database")

    monkeypatch.setattr(seed, "run", _refuse)


def test_flag_off_refuses_before_any_database_work(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    monkeypatch.setattr(settings, "oacp_demo_merchant_enabled", False)
    _no_database(monkeypatch)
    assert seed.main(["--apply"], environ=READY_ENV) == seed.EXIT_REFUSED
    reasons = json.loads(capsys.readouterr().err.strip().splitlines()[-1])["reasons"]
    assert reasons == ["AGENTICORG_OACP_DEMO_MERCHANT_ENABLED is not true in this environment"]


@pytest.mark.usefixtures("demo_on")
@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({**READY_ENV, "AGENTICORG_DB_URL": ""}, "AGENTICORG_DB_URL is not set"),
        (
            {**READY_ENV, "GRANTEX_COMMERCE_INTERNAL_TOKEN": ""},
            "missing Grantex settings: GRANTEX_COMMERCE_INTERNAL_TOKEN",
        ),
        ({**READY_ENV, "PLURAL_PINE_LIVE_EXECUTION_ENABLED": "true"}, "PLURAL_PINE_LIVE_EXECUTION_ENABLED is on"),
        ({**READY_ENV, seed.ADMIN_PASSWORD_ENV: "short"}, "at least 16 characters"),
    ],
)
def test_apply_refusals(monkeypatch: pytest.MonkeyPatch, capsys, environ: dict[str, str], expected: str) -> None:
    _no_database(monkeypatch)
    assert seed.main(["--apply"], environ=environ) == seed.EXIT_REFUSED
    assert expected in capsys.readouterr().err


@pytest.mark.usefixtures("demo_on")
def test_dry_run_and_teardown_need_no_grantex_settings() -> None:
    environ = {"AGENTICORG_DB_URL": READY_ENV["AGENTICORG_DB_URL"]}
    assert seed.check_preconditions(apply=False, teardown=False, environ=environ) == []
    assert seed.check_preconditions(apply=True, teardown=True, environ=environ) == []
    assert seed.check_preconditions(apply=True, teardown=False, environ=READY_ENV) == []


@pytest.mark.parametrize(
    "argv",
    [
        ["--teardown", "--serving-host", "nilgiritea.agenticorg.ai"],
        ["--teardown", "--verify-host"],
        ["--teardown", "--refresh-artifacts"],
        ["--keep-fresh-minutes", "5"],
        ["--apply", "--keep-fresh-minutes", str(seed.MAX_KEEP_FRESH_MINUTES + 1)],
    ],
)
def test_argument_rules(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        seed.parse_args(argv)


def test_default_is_a_dry_run() -> None:
    args = seed.parse_args([])
    assert (args.apply, args.teardown, args.keep_fresh_minutes) == (False, False, 0)


def test_grantex_tenant_not_allowed_names_the_tenant_to_allowlist() -> None:
    response = httpx.Response(403, json={"error": {"code": "service_tenant_not_allowed", "message": "no"}})
    error = seed._grantex_refusal(403, seed._grantex_error_code(response))
    assert error.exit_code == seed.EXIT_GRANTEX_TENANT_NOT_ALLOWED
    assert DEMO_TENANT_ID in str(error) and "COMMERCE_C6Z_AUTHORITY_SERVICE_TENANTS" in str(error)


def test_grantex_without_the_demo_source_stops_without_faking_shopify() -> None:
    response = httpx.Response(
        422, json={"status": "rejected", "refusal_code": "unsupported_or_enabling_authority_request"}
    )
    error = seed._grantex_refusal(422, seed._grantex_error_code(response))
    assert error.exit_code == seed.EXIT_GRANTEX_DEMO_SOURCE_UNSUPPORTED
    assert "Nothing was presented to Grantex as Shopify" in str(error)


def test_other_grantex_answers_fail_with_their_code() -> None:
    assert seed._grantex_error_code(httpx.Response(500, text="not json")) is None
    error = seed._grantex_refusal(202, "source_evidence_stale")
    assert error.exit_code == seed.EXIT_FAILED and "source_evidence_stale" in str(error)


def test_issuance_converges_on_usable_artifacts() -> None:
    state = seed.DemoState(usable_types=dict.fromkeys(seed.ISSUED_ARTIFACT_TYPES, 1))
    assert seed.needs_issuance(state, refresh=False) is False
    assert seed.needs_issuance(state, refresh=True) is True
    state.usable_types.pop("inventory")
    assert seed.needs_issuance(state, refresh=False) is True


def test_dns_record_names_the_txt_host_inside_the_parent_zone() -> None:
    record = seed.dns_record(
        {"record_name": "_agenticorg-ucp.nilgiritea.agenticorg.ai", "record_value": "agenticorg-ucp-verify=abc"}
    )
    assert record == {
        "name": "_agenticorg-ucp.nilgiritea.agenticorg.ai",
        "type": "TXT",
        "value": "agenticorg-ucp-verify=abc",
        "zone": "agenticorg.ai",
        "host_in_zone": "_agenticorg-ucp.nilgiritea",
    }

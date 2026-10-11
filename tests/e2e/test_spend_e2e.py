# SPDX-License-Identifier: Apache-2.0
"""AI spend intelligence end to end against a running stack, over HTTP only.

Runs only when ``AGENTICORG_E2E_BASE_URL`` is set to the stack's origin (no
``/api/v1``), like ``test_smoke.py``; every request goes to
``{base}/api/v1``. Nothing here imports the application.

What runs where:

* Always: unauthenticated spend reads are refused (401).
* With a caller (``AGENTICORG_E2E_TOKEN``, or a fresh administrator this test
  signs up where writes are allowed): ``GET /spend/status``. With the feature
  off (``AGENTICORG_SPEND_INTELLIGENCE_ENABLED`` unset, the default) a sample of
  spend routes must answer 404 ``spend_disabled`` and nothing else runs. With it
  on, read checks run for any caller, and the role-gated reads must either
  answer or refuse with the documented code.
* With the feature on and writes allowed, the full flow: an organisation tree
  (a group root and a department), a source mapping for application
  ``console``, USD to INR rates for the server's day and earlier days, a
  contract rate card for the model the stack's fake LLM answers as, a price
  quote, one metered model call (``POST /agents/generate``, which answers 422
  after the call is metered), the usage record (attributed, priced, converted
  to INR), rollups and coverage, a rollup rebuild job run by the worker, an
  invoice that matches the metered amount, a reconciliation within tolerance,
  the Gate 1 status, the audit rows an administrator reads, and the refusals a
  non-administrator gets.

Production guard: when the base URL's host is ``agenticorg.ai`` or one of its
subdomains, only read checks run (no signup, no writes) unless
``AGENTICORG_E2E_ALLOW_PROD_WRITES=1``; the metered model call is skipped there
even then, because production has no fake model.

Dates come from the server: the ``Date`` header before the call, then the
``event_date`` and ``billing_date`` each usage record reports, so the test
passes in any time zone.

Environment:

* ``AGENTICORG_E2E_BASE_URL``: the origin, e.g. ``http://127.0.0.1:18000`` (the
  API) or the console origin that proxies ``/api``.
* ``AGENTICORG_E2E_TOKEN``: optional; a bearer token to use instead of signing
  up. The write flow needs an active tenant administrator signed in as a
  person, and it leaves reference data behind (spend rows are never deleted),
  so point it at a throwaway tenant. Signup is limited to 5 per IP per hour.
* ``AGENTICORG_E2E_ALLOW_PROD_WRITES=1``: allow the write flow on production.
* ``AGENTICORG_E2E_SPEND_PROVIDER`` / ``AGENTICORG_E2E_SPEND_MODEL``: the provider
  and model the stack's router calls (defaults ``gemini`` /
  ``gemini-2.5-flash``, the ``AGENTICORG_LLM_PRIMARY`` default).
* ``AGENTICORG_E2E_SPEND_MODEL_CALL``: ``auto`` (default: on except on
  production), ``1`` or ``0``.
* ``AGENTICORG_E2E_SPEND_WAIT_SECONDS``: how long to wait for a usage record or
  a job (default 120).

The stack needs ``AGENTICORG_TEST_FAKE_LLM=1`` (a relaxed environment), the
feature switch on for the API and the worker, and a worker consuming the
``maintenance`` queue. Before the model call the write flow registers a
placeholder credential for the provider on the tenant (409 when it already
keeps one), so the call counts as billed to the tenant's own key
(reconciliation compares only tenant-billed usage); the fake model answers
before any credential is read, so the placeholder is never sent anywhere.
Example (Git Bash, the API on port 18000)::

    AGENTICORG_E2E_BASE_URL=http://127.0.0.1:18000 \\
        python -m pytest -q -p no:cacheprovider --no-cov tests/e2e/test_spend_e2e.py

or from the tools container on the stack network, with
``AGENTICORG_E2E_BASE_URL=http://api:8000``.
"""

from __future__ import annotations

import csv
import io
import json
import os
import secrets
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, "") or default)
    except ValueError:
        return default


BASE_URL = os.getenv("AGENTICORG_E2E_BASE_URL", "").strip().rstrip("/")
TOKEN = os.getenv("AGENTICORG_E2E_TOKEN", "").strip()
ALLOW_PROD_WRITES = os.getenv("AGENTICORG_E2E_ALLOW_PROD_WRITES", "").strip() == "1"
PROVIDER = os.getenv("AGENTICORG_E2E_SPEND_PROVIDER", "").strip().lower() or "gemini"
MODEL = os.getenv("AGENTICORG_E2E_SPEND_MODEL", "").strip().lower() or "gemini-2.5-flash"
MODEL_CALL = os.getenv("AGENTICORG_E2E_SPEND_MODEL_CALL", "").strip().lower() or "auto"
WAIT_SECONDS = _env_float("AGENTICORG_E2E_SPEND_WAIT_SECONDS", 120.0)

pytestmark = [
    pytest.mark.skipif(
        not BASE_URL,
        reason="requires AGENTICORG_E2E_BASE_URL (the stack's origin) for the live spend test",
    ),
    # The ini default is 60s per test; a step that waits on the worker needs longer.
    pytest.mark.timeout(int(WAIT_SECONDS) * 2 + 120),
]

PRODUCTION_DOMAIN = "agenticorg.ai"
USD_TO_INR = Decimal("83.25")
UNIT_PRICE = Decimal("1.25")  # USD per million tokens on the contract card this test enters
TOKEN_CARD_UNIT = "1m_tokens"  # unsplit token records (the fake LLM reports no split) price by this unit
PRODUCT_LINE = "spend-e2e"
DESCRIPTION = "Spend end-to-end check: a payables clerk who matches supplier invoices to purchase orders"
AMOUNT_QUANT = Decimal("0.0000000001")
CLOSE_ENOUGH = Decimal("0.000000001")
# The card unit an invoice line names for a record unit, so lines meet their usage on model and unit.
CARD_UNIT_OF = {
    "token": "1m_tokens",
    "input_token": "1m_input_tokens",
    "output_token": "1m_output_tokens",
    "cached_input_token": "1m_cached_input_tokens",
    "embedding_token": "1m_embedding_tokens",
    "ocr_page": "ocr_page",
    "audio_minute": "audio_minute",
    "call": "call",
    "gb_day": "gb_day",
}
_SECRET_KEYS = frozenset({"access_token", "refresh_token", "token", "password", "api_key", "secret", "code"})


# ---------------------------------------------------------------- helpers


def _is_production(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return host == PRODUCTION_DOMAIN or host.endswith("." + PRODUCTION_DOMAIN)


def _strong_password() -> str:
    # Upper, lower and a digit guaranteed around a random core; never printed.
    return "Sp" + secrets.token_urlsafe(18) + "q7"


def _redact(value: Any) -> Any:
    """A response body safe to put in a failure message: tokens, passwords and invite links removed."""
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            name = str(key).lower()
            hidden = name in _SECRET_KEYS or name.endswith(("_token", "password")) or name == "invite_link"
            out[key] = "<redacted>" if hidden else _redact(item)
        return out
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _brief(resp: httpx.Response) -> str:
    try:
        body: Any = _redact(resp.json())
        text = json.dumps(body, default=str)
    except ValueError:
        text = resp.text
    return f"{resp.request.method} {resp.request.url.path} -> {resp.status_code}: {text[:1200]}"


def _error(resp: httpx.Response) -> str | None:
    """The error code of a refusal: ``{"detail": {"error": ...}}``, or the generic envelope's name."""
    try:
        body = resp.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    detail = body.get("detail")
    if isinstance(detail, dict):
        return detail.get("error")
    envelope = body.get("error")
    if isinstance(envelope, dict):
        return envelope.get("name") or envelope.get("code")
    return None


def _dec(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _close(left: Decimal | None, right: Decimal | None) -> bool:
    return left is not None and right is not None and abs(left - right) <= CLOSE_ENOUGH


def _amount(quantity: Decimal, divisor: Decimal, unit_price: Decimal) -> Decimal:
    return (quantity / divisor * unit_price).quantize(AMOUNT_QUANT, rounding=ROUND_HALF_EVEN)


def _month_after(first: date) -> date:
    return (first.replace(day=28) + timedelta(days=4)).replace(day=1)


def _month_windows(period: str) -> list[tuple[date, date]]:
    """Reporting-day windows (at most 31 days each) that hold every record billed in ``period``.

    A billing zone and the reporting zone are at most a day apart, so the
    month's first day less one through the next month's first day covers it.
    """
    first = date.fromisoformat(f"{period}-01")
    start, end = first - timedelta(days=1), _month_after(first)
    windows = []
    while start <= end:
        stop = min(start + timedelta(days=30), end)
        windows.append((start, stop))
        start = stop + timedelta(days=1)
    return windows


class Api:
    """A bearer client for ``{base}/api/v1`` that keeps no cookies and notes the server's clock."""

    def __init__(self, token: str | None = None) -> None:
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(base_url=f"{BASE_URL}/api/v1", headers=headers, timeout=60.0)
        self.server_now: datetime | None = None

    def close(self) -> None:
        self._client.close()

    def request(
        self, method: str, path: str, *, expect: int | tuple[int, ...] | None = None, **kwargs: Any
    ) -> httpx.Response:
        attempts = 3 if method == "GET" else 1  # never repeat a write
        resp: httpx.Response | None = None
        failure = ""
        for attempt in range(attempts):
            try:
                resp = self._client.request(method, path, **kwargs)
            except httpx.TransportError as exc:
                failure = f"{method} {path}: {type(exc).__name__} talking to {BASE_URL}"
                if attempt < attempts - 1:
                    time.sleep(2)
                continue
            finally:
                # A signup or login sets a session cookie; requests here authenticate by bearer only.
                self._client.cookies.clear()
            if resp.status_code in (502, 504) and attempt < attempts - 1:
                time.sleep(2)
                continue
            break
        if resp is None:
            pytest.fail(failure)
        stamp = resp.headers.get("date")
        if stamp:
            try:
                self.server_now = parsedate_to_datetime(stamp).astimezone(UTC)
            except (TypeError, ValueError):
                pass
        if expect is not None:
            allowed = (expect,) if isinstance(expect, int) else expect
            assert resp.status_code in allowed, _brief(resp)
        return resp

    def json(self, method: str, path: str, *, expect: int | tuple[int, ...] = 200, **kwargs: Any) -> Any:
        return self.request(method, path, expect=expect, **kwargs).json()

    def server_today(self) -> date:
        """The server's UTC date, from its ``Date`` header (the test machine's clock is not used)."""
        if self.server_now is None:
            self.request("GET", "/health")
        return (self.server_now or datetime.now(UTC)).date()

    def usage(self, start: date, end: date, **filters: Any) -> list[dict[str, Any]]:
        """Every usage record of reporting days ``[start, end]`` (at most 31) matching the filters."""
        items: list[dict[str, Any]] = []
        cursor = None
        for _page in range(100):
            params: dict[str, Any] = {"start": start.isoformat(), "end": end.isoformat(), "limit": 500, **filters}
            if cursor:
                params["cursor"] = cursor
            page = self.json("GET", "/spend/usage", params=params)
            items.extend(page["items"])
            cursor = page.get("next_cursor")
            if not cursor:
                return items
        pytest.fail("more than 100 pages of usage records in one window")

    def wait_job(self, job_id: str) -> dict[str, Any]:
        deadline = time.monotonic() + WAIT_SECONDS
        while True:
            job = self.json("GET", f"/spend/jobs/{job_id}")
            if job["status"] in ("succeeded", "failed"):
                return job
            if time.monotonic() > deadline:
                pytest.fail(
                    f"spend job {job['kind']} still {job['status']} after {WAIT_SECONDS:.0f}s: "
                    "is a worker consuming the maintenance queue with spend on?"
                )
            time.sleep(1.0)

    def wait_jobs_idle(self, kind: str | None = None) -> list[dict[str, Any]]:
        deadline = time.monotonic() + WAIT_SECONDS
        params: dict[str, Any] = {"limit": 50}
        if kind:
            params["kind"] = kind
        while True:
            jobs = self.json("GET", "/spend/jobs", params=params)["items"]
            busy = [job for job in jobs if job["status"] in ("queued", "running")]
            if not busy:
                return jobs
            if time.monotonic() > deadline:
                listed = ", ".join(f"{job['kind']} {job['status']}" for job in busy)
                pytest.fail(
                    f"spend jobs still busy after {WAIT_SECONDS:.0f}s ({listed}): "
                    "is a worker consuming the maintenance queue with spend on?"
                )
            time.sleep(1.0)


@dataclass
class Live:
    production: bool
    writes: bool
    model_call: bool
    run: str
    admin: Api | None = None
    signed_up: bool = False
    status_resp: httpx.Response | None = None
    no_caller: str = ""
    caller_failed: bool = False
    state: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> dict[str, Any]:
        assert self.status_resp is not None
        return self.status_resp.json()

    @property
    def enabled(self) -> bool:
        return (
            self.status_resp is not None and self.status_resp.status_code == 200 and self.status.get("enabled") is True
        )


def _sign_up(run: str) -> tuple[str | None, str, bool]:
    """A fresh tenant administrator's token, or ``(None, why, failed)``."""
    body = {
        "org_name": f"Spend E2E {run}",
        "admin_name": "Spend E2E Admin",
        "admin_email": f"spend-e2e-{run}@example.com",
        "password": _strong_password(),
    }
    with httpx.Client(base_url=f"{BASE_URL}/api/v1", timeout=60.0) as client:
        try:
            resp = client.post("/auth/signup", json=body)
        except httpx.TransportError as exc:
            return None, f"signup: {type(exc).__name__} talking to {BASE_URL}", True
    if resp.status_code == 429:
        return (
            None,
            "signup is rate limited (5 per IP per hour): set AGENTICORG_E2E_TOKEN to an administrator "
            "token of a throwaway tenant",
            False,
        )
    if resp.status_code != 201 or not resp.json().get("access_token"):
        return None, f"signup failed: {_brief(resp)}", True
    return str(resp.json()["access_token"]), "", False


@pytest.fixture(scope="module")
def live() -> Iterator[Live]:
    production = _is_production(BASE_URL)
    writes = not production or ALLOW_PROD_WRITES
    model_call = (not production) if MODEL_CALL == "auto" else MODEL_CALL in ("1", "true", "yes")
    state = Live(production=production, writes=writes, model_call=model_call, run=uuid.uuid4().hex[:10])
    if TOKEN:
        state.admin = Api(TOKEN)
    elif writes:
        token, why, failed = _sign_up(state.run)
        if token:
            state.admin, state.signed_up = Api(token), True
        else:
            state.no_caller, state.caller_failed = why, failed
    else:
        state.no_caller = (
            "read-only against production with no AGENTICORG_E2E_TOKEN: only the unauthenticated checks run"
        )
    if state.admin is not None:
        state.status_resp = state.admin.request("GET", "/spend/status")
    try:
        yield state
    finally:
        if state.admin is not None:
            state.admin.close()
        reader = state.state.get("reader")
        if isinstance(reader, Api):
            reader.close()


def _need_caller(live: Live) -> Api:
    if live.admin is None:
        if live.caller_failed:
            pytest.fail(live.no_caller)
        pytest.skip(live.no_caller)
    assert live.status_resp is not None and live.status_resp.status_code == 200, (
        _brief(live.status_resp) if live.status_resp is not None else "no status read"
    )
    return live.admin


def _need_enabled(live: Live) -> Api:
    api = _need_caller(live)
    if not live.enabled:
        pytest.skip("spend intelligence is off on this stack (AGENTICORG_SPEND_INTELLIGENCE_ENABLED)")
    return api


def _need_writes(live: Live, *keys: str) -> Api:
    api = _need_enabled(live)
    if not live.writes:
        pytest.skip("read-only against production: set AGENTICORG_E2E_ALLOW_PROD_WRITES=1 to run the write flow")
    if live.state.get("not_admin"):
        pytest.skip("the caller is not an active tenant administrator signed in as a person")
    missing = [key for key in keys if key not in live.state]
    if missing:
        pytest.skip(f"an earlier step did not complete ({', '.join(missing)})")
    return api


def _refused(resp: httpx.Response, status: int, code: str) -> None:
    assert resp.status_code == status and _error(resp) == code, f"expected {status} {code}; got {_brief(resp)}"


# ---------------------------------------------------------------- always


def test_unauthenticated_spend_reads_are_refused() -> None:
    api = Api()
    try:
        today = api.server_today()
        for path, params in (
            ("/spend/status", None),
            ("/spend/usage", {"start": today.isoformat(), "end": today.isoformat()}),
        ):
            resp = api.request("GET", path, params=params)
            assert resp.status_code == 401, f"an unauthenticated read must be refused: {_brief(resp)}"
    finally:
        api.close()


def test_status_reports_the_switch_and_the_bounds(live: Live) -> None:
    _need_caller(live)
    status = live.status
    assert isinstance(status.get("enabled"), bool), status
    assert status["reporting_currency"] == "INR"
    assert status["reporting_timezone"], status
    assert {
        "llm_tokens",
        "embedding_tokens",
        "ocr_pages",
        "speech_minutes",
        "tool_calls",
        "storage",
        "gpu_hours",
    } <= set(status["usage_types"])
    assert "console" in status["applications"]
    assert status["limits"]["usage_window_days"] == 31
    assert status["limits"]["rebuild_days"] == 31
    assert status["limits"]["restate_days"] == 92
    assert status["limits"]["gpu_allocation_days"] == 31
    # Whether this process's writer started; the pending count spans every tenant and is not given.
    assert set(status["writer"]) == {"started"}, status["writer"]


def test_flag_off_routes_answer_spend_disabled(live: Live) -> None:
    api = _need_caller(live)
    if live.enabled:
        pytest.skip("spend intelligence is on here; the flag-off contract runs against a stack with it off")
    today = api.server_today()
    day = {"start": today.isoformat(), "end": today.isoformat()}
    samples: list[tuple[str, str, dict[str, Any]]] = [
        ("GET", "/spend/org-nodes", {}),
        ("GET", "/spend/mappings", {}),
        ("GET", "/spend/rate-cards", {}),
        ("GET", "/spend/usage", {"params": day}),
        ("GET", "/spend/rollups", {"params": day}),
        ("GET", "/spend/coverage", {"params": day}),
        ("GET", "/spend/jobs", {}),
        ("GET", "/spend/invoices", {}),
        ("GET", "/spend/gate", {"params": {"period": today.strftime("%Y-%m")}}),
    ]
    if live.signed_up:
        # As the fresh administrator, with invalid bodies: answered 404 before the body is read while off,
        # and 422 (never a write) if the switch were on.
        samples += [
            ("POST", "/spend/org-nodes", {"json": {}}),
            ("PUT", "/spend/fx-rates", {"json": {}}),
            ("POST", "/spend/reconciliations", {"json": {}}),
            ("POST", "/spend/rate-cards/import", {}),
        ]
    for method, path, kwargs in samples:
        _refused(api.request(method, path, **kwargs), 404, "spend_disabled")
    # The status route still answers, and says so.
    again = api.json("GET", "/spend/status")
    assert again["enabled"] is False


# ---------------------------------------------------------------- reads, any caller


def test_read_checks_and_role_gated_reads(live: Live) -> None:
    api = _need_enabled(live)
    today = api.server_today()
    window = {"start": (today - timedelta(days=1)).isoformat(), "end": (today + timedelta(days=1)).isoformat()}
    assert isinstance(api.json("GET", "/spend/org-nodes")["items"], list)
    assert isinstance(api.json("GET", "/spend/mappings")["items"], list)
    assert isinstance(api.json("GET", "/spend/fx-rates")["items"], list)
    page = api.json("GET", "/spend/usage", params=window)
    assert isinstance(page["items"], list) and "next_cursor" in page
    rollups = api.json("GET", "/spend/rollups", params=window)
    assert rollups["group_by"] == "day" and "totals" in rollups
    too_long = {"start": (today - timedelta(days=31)).isoformat(), "end": today.isoformat()}
    _refused(api.request("GET", "/spend/usage", params=too_long), 422, "range_too_long")

    coverage = api.request("GET", "/spend/coverage", params=window)
    jobs = api.request("GET", "/spend/jobs")
    for resp in (coverage, jobs):
        if resp.status_code != 200:
            _refused(resp, 403, "tenant_wide_read_refused")
    cards = api.request("GET", "/spend/rate-cards")
    gate = api.request("GET", "/spend/gate", params={"period": today.strftime("%Y-%m")})
    for resp in (cards, gate):
        if resp.status_code != 200:
            _refused(resp, 403, "commercial_read_refused")
    # A commercial reader (a human administrator or auditor) is also a tenant-wide reader.
    assert (cards.status_code == 200) == (gate.status_code == 200), (_brief(cards), _brief(gate))
    if cards.status_code == 200:
        assert coverage.status_code == 200 and jobs.status_code == 200, (_brief(coverage), _brief(jobs))
        body = gate.json()
        assert isinstance(body["gate_met"], bool)
        assert body["attribution"]["target"] in ("0.98", "0.980000"), body["attribution"]["target"]
        assert "backfill_source" in body
    if coverage.status_code == 200:
        period = coverage.json()["period"]
        assert {"attributed_share", "unattributed_share", "unpriced_count", "gaps"} <= set(period)


# ---------------------------------------------------------------- the write flow


def test_reference_data_is_kept(live: Live) -> None:
    api = _need_writes(live)
    state = live.state
    today = api.server_today()
    group_code = f"E2E-{live.run}-GRP".upper()
    dept_code = f"E2E-{live.run}-FIN".upper()

    root = api.request(
        "POST", "/spend/org-nodes", json={"code": group_code, "name": f"Spend E2E {live.run}", "kind": "group"}
    )
    if root.status_code == 403:
        state["not_admin"] = True
        pytest.skip(f"the caller cannot write spend reference data: {_brief(root)}")
    assert root.status_code == 201, _brief(root)
    dept = api.json(
        "POST",
        "/spend/org-nodes",
        expect=201,
        json={
            "code": dept_code,
            "name": f"Spend E2E finance {live.run}",
            "kind": "department",
            "parent_code": group_code,
        },
    )
    assert dept["parent_code"] == group_code and dept["kind"] == "department", dept
    node = api.json("GET", f"/spend/org-nodes/{dept['id']}")
    assert [a["code"] for a in node["ancestors"]] == [group_code], node

    mapping = api.json(
        "PUT",
        "/spend/mappings",
        json={
            "source_type": "application",
            "source_ref": "console",
            "org_node_code": dept_code,
            "product_line": PRODUCT_LINE,
        },
    )
    assert mapping["org_node_id"] == dept["id"] and mapping["active"] is True, mapping

    # The tenant's own key for the provider, so the call is tenant-billed. A placeholder: the fake LLM
    # answers before any credential is read. 409 means the tenant already keeps one.
    if live.model_call:
        cred = api.request(
            "POST",
            "/tenant-ai-credentials",
            json={
                "provider": PROVIDER,
                "credential_kind": "llm",
                "api_key": "e2e-placeholder-" + secrets.token_hex(12),
                "label": "spend e2e placeholder",
            },
        )
        assert cred.status_code in (201, 409), _brief(cred)
        state["credential"] = cred.status_code == 201

    # USD to INR for the server's day, the days either side (the reporting day is within a day of UTC)
    # and an earlier day. A rate settled records already use answers 409 fx_in_use: keep that one.
    rates: dict[str, Decimal] = {}
    settle_jobs: set[str] = set()
    for offset in (-7, -1, 0, 1):
        day = (today + timedelta(days=offset)).isoformat()
        resp = api.request(
            "PUT", "/spend/fx-rates", json={"rate_date": day, "currency": "USD", "rate_to_inr": str(USD_TO_INR)}
        )
        if resp.status_code == 409 and _error(resp) == "fx_in_use":
            kept = api.json("GET", "/spend/fx-rates", params={"currency": "USD", "start": day, "end": day})["items"]
            rates[day] = Decimal(kept[0]["rate_to_inr"])
            continue
        assert resp.status_code == 200, _brief(resp)
        body = resp.json()
        assert body["currency"] == "USD" and body["rate_date"] == day, body
        rates[day] = Decimal(body["rate_to_inr"])
        if body.get("settle_job_id"):
            settle_jobs.add(body["settle_job_id"])

    # A contract card for the model, reused when an earlier run on this tenant left one in force.
    billing_day = today - timedelta(days=1)
    cards = api.json(
        "GET",
        "/spend/rate-cards",
        params={"provider": PROVIDER, "usage_type": "llm_tokens", "as_of": billing_day.isoformat()},
    )["items"]
    card = next(
        (c for c in cards if c["model_sku"] == MODEL and c["unit"] == TOKEN_CARD_UNIT and c["source"] == "contract"),
        None,
    )
    if card is None:
        card = api.json(
            "POST",
            "/spend/rate-cards",
            expect=201,
            json={
                "provider": PROVIDER,
                "usage_type": "llm_tokens",
                "model_sku": MODEL,
                "unit": TOKEN_CARD_UNIT,
                "unit_price": str(UNIT_PRICE),
                "currency": "USD",
                "effective_from": (today - timedelta(days=40)).isoformat(),
                "source": "contract",
                "reference": f"spend e2e {live.run}",
            },
        )
    assert card["provider"] == PROVIDER and card["status"] == "active", card

    state.update(
        today=today,
        group_code=group_code,
        dept_code=dept_code,
        dept_id=dept["id"],
        card_id=card["id"],
        card_currency=card["currency"],
        card_price=Decimal(card["unit_price"]),
        card_from=date.fromisoformat(card["effective_from"]),
        rates=rates,
        settle_jobs=settle_jobs,
    )


def test_price_quote_uses_the_contract_card(live: Live) -> None:
    api = _need_writes(live, "card_id", "rates")
    state = live.state
    today: date = state["today"]
    params = {
        "provider": PROVIDER,
        "usage_type": "llm_tokens",
        "unit": "token",
        "quantity": "1000000",
        "model": MODEL,
        "on": (today - timedelta(days=1)).isoformat(),
        "fx_on": today.isoformat(),
    }
    quote = api.json("GET", "/spend/price", params=params)
    assert quote["price_source"] == "contract" and quote["rate_card_id"] == state["card_id"], quote
    assert quote["currency"] == state["card_currency"], quote
    assert _close(_dec(quote["amount"]), state["card_price"]), quote
    if state["card_currency"] == "INR":
        assert _close(_dec(quote["amount_inr"]), state["card_price"]), quote
    else:
        rate = state["rates"][today.isoformat()]
        assert quote["fx_rate_date"] == today.isoformat(), quote
        assert _close(
            _dec(quote["amount_inr"]), _amount(Decimal(1_000_000), Decimal(1_000_000), state["card_price"] * rate)
        ), quote

    # Effective dating: the day before the card starts, the contract card does not price.
    before = dict(params, on=(state["card_from"] - timedelta(days=1)).isoformat())
    earlier = api.json("GET", "/spend/price", params=before)
    assert earlier["rate_card_id"] != state["card_id"] and earlier["price_source"] != "contract", earlier


def test_model_call_is_metered_attributed_and_priced(live: Live) -> None:
    api = _need_writes(live, "dept_id", "card_id")
    if not live.model_call:
        pytest.skip("the metered model call needs the stack's fake LLM (AGENTICORG_E2E_SPEND_MODEL_CALL)")
    state = live.state
    resp = api.request("POST", "/agents/generate", json={"description": DESCRIPTION})
    assert resp.status_code != 503, f"the stack has no model: run it with AGENTICORG_TEST_FAKE_LLM=1 ({_brief(resp)})"
    # The fake LLM's answer holds no suggestions, so the route answers 422 after the call was metered.
    assert resp.status_code in (200, 422), _brief(resp)
    called_at = (api.server_now or datetime.now(UTC)) - timedelta(minutes=2)
    today = api.server_today()

    def ours() -> list[dict[str, Any]]:
        records = api.usage(
            today - timedelta(days=1), today + timedelta(days=1), provider=PROVIDER, usage_type="llm_tokens"
        )
        return [
            r for r in records if r["application"] == "console" and datetime.fromisoformat(r["event_time"]) >= called_at
        ]

    deadline = time.monotonic() + min(WAIT_SECONDS, 60.0)
    records = ours()
    while not records and time.monotonic() < deadline:
        time.sleep(1.0)
        records = ours()
    assert records, (
        f"no {PROVIDER} usage record for the call within {min(WAIT_SECONDS, 60.0):.0f}s: is spend on for the API, "
        f"and is AGENTICORG_E2E_SPEND_MODEL the stack's AGENTICORG_LLM_PRIMARY? See /spend/gaps for failed calls."
    )
    time.sleep(1.5)  # a retried generation writes a second call; let it land
    records = ours()

    for record in records:
        where = f"record {record['id']}"
        assert record["model"] == MODEL, f"{where}: model {record['model']} (set AGENTICORG_E2E_SPEND_MODEL)"
        # Attributed through the console mapping to the department, server-side.
        assert record["org_node_id"] == state["dept_id"], f"{where}: {record}"
        assert record["attribution_path"] == "application_mapping" and record["unattributed_reason"] is None, record
        assert record["product_line"] == PRODUCT_LINE and record["agent_id"] is None, record
        assert record["use_case"] == "agent.generate", f"{where}: use case {record['use_case']}"
        # Priced by the contract card in its currency.
        assert "unpriced" not in record["flags"], f"{where} is unpriced (records unit {record['unit']}): {record}"
        assert record["price_source"] == "contract" and record["rate_card_id"] == state["card_id"], record
        assert record["currency"] == state["card_currency"], record
        assert _dec(record["unit_price"]) == state["card_price"], record
        assert record["unit"] == "token", f"{where}: the fake LLM reports an unsplit count; got {record['unit']}"
        expected = _amount(Decimal(record["quantity"]), Decimal(1_000_000), state["card_price"])
        assert _close(_dec(record["amount"]), expected), f"{where}: amount {record['amount']}, expected {expected}"
        # Converted to INR on its reporting day, at that day's rate.
        assert record["amount_inr"] is not None and "unconverted" not in record["flags"], record
        if state["card_currency"] != "INR":
            assert "fx_estimated" not in record["flags"], f"{where}: no rate for {record['event_date']}: {record}"
            assert record["fx_rate_date"] == record["event_date"], record
            assert _dec(record["fx_rate"]) == state["rates"].get(record["event_date"]), record
            inr = (Decimal(record["amount"]) * Decimal(record["fx_rate"])).quantize(AMOUNT_QUANT, ROUND_HALF_EVEN)
            assert _close(_dec(record["amount_inr"]), inr), (
                f"{where}: amount_inr {record['amount_inr']}, expected {inr}"
            )
        if state.get("credential"):
            assert record["billing_account"] == "tenant_key", f"{where}: {record['billing_account']}"

    state["records"] = records
    state["event_days"] = sorted({date.fromisoformat(r["event_date"]) for r in records})


def test_rollups_and_coverage_include_the_call(live: Live) -> None:
    api = _need_writes(live, "records")
    state = live.state
    records: list[dict[str, Any]] = state["records"]
    days: list[date] = state["event_days"]
    window = {"start": days[0].isoformat(), "end": days[-1].isoformat()}
    currency = state["card_currency"]
    ours_amount = sum((Decimal(r["amount"]) for r in records), Decimal(0))

    by_node = api.json("GET", "/spend/rollups", params={**window, "group_by": "org_node_id", "provider": PROVIDER})
    row = next((r for r in by_node["rows"] if r["org_node_id"] == state["dept_id"]), None)
    assert row is not None, by_node
    assert row["records"] >= len(records) and row["calls"] >= 1, row
    assert _dec(row["amount_by_currency"].get(currency)) is not None, row
    if live.signed_up:
        assert row["records"] == len(records), row
        assert _close(_dec(row["amount_by_currency"][currency]), ours_amount), (row, ours_amount)
        assert row["unpriced"] == 0 and row["unconverted"] == 0, row

    by_app = api.json("GET", "/spend/rollups", params={**window, "group_by": "application", "application": "console"})
    assert [r["application"] for r in by_app["rows"]] == ["console"], by_app
    # The administrator may group by rate card (a commercial grouping).
    by_card = api.json("GET", "/spend/rollups", params={**window, "group_by": "rate_card_id", "provider": PROVIDER})
    assert state["card_id"] in {r["rate_card_id"] for r in by_card["rows"]}, by_card

    coverage = api.json("GET", "/spend/coverage", params=window)
    period = coverage["period"]
    assert period["records"] >= len(records), period
    assert {d["day"] for d in coverage["days"]} >= {d.isoformat() for d in days}, coverage["days"]
    assert any(
        p["attribution_path"] == "application_mapping" and p["node_kind"] == "department" for p in coverage["by_path"]
    ), coverage["by_path"]
    if live.signed_up:
        # Only this call's records: the whole amount sits on a department, nothing unpriced or unconverted.
        assert period["attributed_share"] == "1.000000", period
        assert period["unattributed_share"] == "0.000000", period
        assert period["unpriced_count"] == 0 and period["unconverted_count"] == 0, period
        assert period["fx_pending_count"] == 0, period

    state["day_totals"] = api.json("GET", "/spend/rollups", params={**window, "group_by": "day"})["totals"]


def test_rollup_rebuild_job_runs_on_the_worker(live: Live) -> None:
    api = _need_writes(live, "records", "day_totals")
    state = live.state
    days: list[date] = state["event_days"]
    window = {"start": days[0].isoformat(), "end": days[-1].isoformat()}
    api.wait_jobs_idle(kind="rebuild")
    accepted = api.json("POST", "/spend/rollups/rebuild", expect=202, json=window)
    assert accepted["status"] == "queued", f"the rebuild could not be sent to a worker: {accepted}"
    job = api.wait_job(accepted["job_id"])
    assert job["kind"] == "rebuild" and job["status"] == "succeeded", job
    assert job["error_code"] == "", job
    assert job["params"]["start"] == window["start"] and job["params"]["end"] == window["end"], job
    listed = api.json("GET", "/spend/jobs", params={"kind": "rebuild"})["items"]
    assert accepted["job_id"] in {j["id"] for j in listed}, listed

    # A rebuilt day equals the day kept incrementally.
    after = api.json("GET", "/spend/rollups", params={**window, "group_by": "day"})["totals"]
    before = state["day_totals"]
    if live.signed_up:
        assert after["records"] == before["records"] and after["calls"] == before["calls"], (before, after)
        assert after["amount_by_currency"] == before["amount_by_currency"], (before, after)
        assert after["amount_inr"] == before["amount_inr"], (before, after)
    else:
        assert after["records"] >= before["records"], (before, after)
    state["rebuild_job"] = accepted["job_id"]


def test_invoice_reconciles_within_tolerance(live: Live) -> None:
    api = _need_writes(live, "records", "rebuild_job")
    state = live.state
    records: list[dict[str, Any]] = state["records"]
    # The provider's billing month, from the billing dates the records carry.
    months: dict[str, int] = {}
    for record in records:
        months[record["billing_date"][:7]] = months.get(record["billing_date"][:7], 0) + 1
    period = max(months, key=lambda month: months[month])

    # Every job the flow queued has finished, so nothing changes the month after the run reads it.
    api.wait_jobs_idle()
    for job_id in sorted(state["settle_jobs"]):
        job = api.json("GET", f"/spend/jobs/{job_id}")
        assert job["status"] == "succeeded", f"FX settlement job: {job}"

    # Everything billed to the tenant for the provider in that month, this run's call and any earlier.
    month: dict[str, dict[str, Any]] = {}
    for start, end in _month_windows(period):
        for record in api.usage(start, end, provider=PROVIDER):
            if record["billing_date"].startswith(period):
                month[record["id"]] = record
    compared = [r for r in month.values() if r["billing_account"] not in ("platform_key", "in_house")]
    strict = live.signed_up and state.get("credential")
    if not compared:
        message = "the call was billed to the platform's key, which is outside the tenant's invoice"
        if strict:
            pytest.fail(message)
        pytest.skip(message + " (no tenant credential for the provider)")
    currencies = {r["currency"] for r in compared}
    unpriced = [r["id"] for r in compared if "unpriced" in r["flags"]]
    if unpriced or len(currencies) != 1:
        message = (
            f"the tenant's {period} {PROVIDER} usage has {len(unpriced)} unpriced record(s) and currencies "
            f"{sorted(c or '' for c in currencies)}"
        )
        if strict:
            pytest.fail(message)
        pytest.skip(message + "; no single-currency invoice can match it")
    currency = currencies.pop()
    invoice_ref = f"SPEND-E2E-{PROVIDER.upper()}"
    current = api.json("GET", "/spend/invoices", params={"provider": PROVIDER, "period": period})["items"]
    others = [i for i in current if i["status"] == "current" and i["invoice_ref"] != invoice_ref]
    if others:
        pytest.skip(f"the tenant keeps other current {PROVIDER} invoices for {period}; they are summed with this one")

    # One usage line per usage type, model and card unit, amounts summed exactly (at most 10 decimals each).
    lines: dict[tuple[str, str, str], Decimal] = {}
    for record in compared:
        key = (record["usage_type"], record["model"], CARD_UNIT_OF.get(record["unit"], ""))
        lines[key] = lines.get(key, Decimal(0)) + Decimal(record["amount"])
    total = sum(lines.values(), Decimal(0))
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["line_kind", "usage_type", "model_sku", "unit", "amount"])
    for (usage_type, model, unit), amount in sorted(lines.items()):
        writer.writerow(["usage", usage_type, model, unit, format(amount, "f")])
    upload = buffer.getvalue().encode("utf-8")
    params = {
        "provider": PROVIDER,
        "period": period,
        "invoice_ref": invoice_ref,
        "currency": currency,
        "replace": "true",
    }

    preview = api.json(
        "POST",
        "/spend/invoices/import",
        params={**params, "dry_run": "true"},
        files={"file": ("invoice.csv", upload, "text/csv")},
    )
    assert preview["invoice_id"] is None and not preview["rejected"], preview
    imported = api.json(
        "POST", "/spend/invoices/import", params=params, files={"file": ("invoice.csv", upload, "text/csv")}
    )
    assert imported["invoice_id"] and imported["line_count"] == len(lines), imported
    assert _dec(imported["usage_amount"]) == total, (imported, total)

    run = api.json("POST", "/spend/reconciliations", expect=201, json={"provider": PROVIDER, "period": period})
    assert run["status"] == "within_tolerance", f"reconciliation: {_redact(run)}"
    assert run["needs_review_count"] == 0 and run["superseded"] is False, run
    assert _dec(run["invoice_amount"]) == total and _dec(run["stored_amount"]) == total, run
    repriced_pct = _dec(run["repriced_variance_pct"])
    assert run["repriced_amount"] is not None and repriced_pct is not None and abs(repriced_pct) <= 1, run
    assert state["card_id"] in run["card_ids"], run
    assert imported["invoice_id"] in run["invoice_ids"], run
    usage_items = [item for item in run["items"] if item["item_kind"] == "usage"]
    assert usage_items and all(item["status"] == "within_tolerance" for item in usage_items), run["items"]
    fetched = api.json("GET", f"/spend/reconciliations/{run['id']}")
    assert fetched["stale"] is False and fetched["status"] == "within_tolerance", fetched

    # Nothing to accept: acceptance is for a current run out of tolerance.
    accept = api.request(
        "POST", f"/spend/reconciliations/{run['id']}/accept", json={"reason": "End-to-end check: nothing to accept"}
    )
    _refused(accept, 409, "not_reviewable")
    state.update(period=period, reconciliation_id=run["id"], invoice_id=imported["invoice_id"])


def test_gate_status_for_the_month(live: Live) -> None:
    api = _need_writes(live, "reconciliation_id")
    state = live.state
    period: str = state["period"]
    gate = api.json("GET", "/spend/gate", params={"period": period})
    assert gate["period"] == period and "backfill_source" in gate, gate
    entry = next((p for p in gate["reconciliation"]["providers"] if p["provider"] == PROVIDER), None)
    assert entry is not None, gate["reconciliation"]
    assert entry["reconciliation_id"] == state["reconciliation_id"], entry
    assert entry["status"] == "within_tolerance" and entry["stale"] is False, entry
    assert PROVIDER not in gate["reconciliation"]["missing_reconciliations"], gate["reconciliation"]
    if not live.signed_up:
        return
    assert gate["reconciliation"]["met"] is True, gate["reconciliation"]
    # The attribution measure reads the reporting month; at a month boundary it can differ from the billing month.
    if all(day.strftime("%Y-%m") == period for day in state["event_days"]):
        attribution = gate["attribution"]
        assert attribution["met"] is True and attribution["reasons"] == [], attribution
        assert attribution["attributed_share"] == "1.000000", attribution
        assert gate["gate_met"] is True, gate


def test_audit_shows_spend_rows_to_the_administrator(live: Live) -> None:
    api = _need_writes(live, "dept_id")
    expected = ["spend.org_node.create", "spend.mappings.", "spend.fx_rates.", "spend.rate_cards."]
    if "rebuild_job" in live.state:
        expected.append("spend.job.enqueue")
    if "reconciliation_id" in live.state:
        expected += ["spend.invoices.import", "spend.reconciliations.run"]
    for event_type in expected:
        page = api.json("GET", "/audit", params={"event_type": event_type, "per_page": 5})
        assert page["total"] >= 1 and page["items"], f"GET /audit hides {event_type} from the administrator"
        assert all(item["event_type"].startswith(event_type) for item in page["items"]), page["items"]


def test_non_admin_reads_are_filtered_and_refused(live: Live) -> None:
    api = _need_writes(live, "records")
    state = live.state
    invite = api.request(
        "POST",
        "/org/invite",
        json={
            "email": f"spend-e2e-reader-{live.run}@example.com",
            "name": "Spend E2E Reader",
            "role": "domain_lead",
            "domain": "finance",
        },
    )
    if invite.status_code != 201:
        pytest.skip(f"no non-administrator could be created cheaply: {_brief(invite)}")
    codes = parse_qs(urlsplit(str(invite.json().get("invite_link") or "")).query).get("code")
    if not codes:
        pytest.skip("the invite answered no invite code")
    anonymous = Api()
    try:
        accept = anonymous.request(
            "POST",
            "/org/accept-invite",
            json={"code": codes[0], "password": _strong_password(), "name": "Spend E2E Reader"},
        )
    finally:
        anonymous.close()
    if accept.status_code != 200 or not accept.json().get("access_token"):
        pytest.skip(f"the invite could not be accepted: {_brief(accept)}")
    reader = Api(str(accept.json()["access_token"]))
    state["reader"] = reader

    records: list[dict[str, Any]] = state["records"]
    days: list[date] = state["event_days"]
    window = {"start": days[0].isoformat(), "end": days[-1].isoformat()}
    # Usage: a record with no agent is visible to every reader, without its contract terms.
    seen = {r["id"]: r for r in reader.usage(days[0], days[-1], provider=PROVIDER)}
    for record in records:
        mine = seen.get(record["id"])
        assert mine is not None, f"a record with no agent is hidden from the domain reader: {record['id']}"
        assert mine["rate_card_id"] is None and mine["unit_price"] is None and mine["commitment_id"] is None, mine
        assert "overage" not in mine["flags"] and mine["initiating_user_id"] is None, mine
        assert mine["amount"] == record["amount"] and mine["amount_inr"] == record["amount_inr"], (mine, record)
    assert reader.json("GET", "/spend/rollups", params={**window, "group_by": "day"})["totals"]["records"] >= len(
        records
    )

    period = state.get("period") or days[0].strftime("%Y-%m")
    quote = {
        "provider": PROVIDER,
        "usage_type": "llm_tokens",
        "unit": "token",
        "quantity": "1000",
        "model": MODEL,
        "on": days[0].isoformat(),
    }
    for path, params in (
        ("/spend/rate-cards", None),
        ("/spend/commitments", None),
        ("/spend/price", quote),
        ("/spend/invoices", None),
        ("/spend/reconciliations", None),
        ("/spend/gate", {"period": period}),
        ("/spend/rollups", {**window, "group_by": "rate_card_id"}),
    ):
        _refused(reader.request("GET", path, params=params), 403, "commercial_read_refused")
    for path, params in (
        ("/spend/coverage", window),
        ("/spend/gaps", window),
        ("/spend/jobs", None),
    ):
        _refused(reader.request("GET", path, params=params), 403, "tenant_wide_read_refused")
    if "rebuild_job" in state:
        _refused(reader.request("GET", f"/spend/jobs/{state['rebuild_job']}"), 403, "tenant_wide_read_refused")

    # Writes are for a tenant administrator.
    write = reader.request(
        "POST",
        "/spend/org-nodes",
        json={
            "code": f"E2E-{live.run}-READER".upper(),
            "name": "refused",
            "kind": "team",
            "parent_code": state["dept_code"],
        },
    )
    assert write.status_code == 403, f"a domain reader wrote reference data: {_brief(write)}"

    # The audit rows of rate cards carry prices: hidden from this reader if it may read the audit at all.
    audit = reader.request("GET", "/audit", params={"event_type": "spend.rate_cards", "per_page": 5})
    if audit.status_code == 200:
        assert audit.json()["total"] == 0 and audit.json()["items"] == [], "rate-card audit rows reach a domain reader"
    else:
        assert audit.status_code == 403, _brief(audit)

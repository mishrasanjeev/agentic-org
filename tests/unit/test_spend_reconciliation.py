# SPDX-License-Identifier: Apache-2.0
"""Spend invoices, reconciliation, acceptance and the Gate 1 status."""

from __future__ import annotations

import asyncio
import csv
import importlib.util
import io
import json
import re
import uuid
from dataclasses import replace as replace_card
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import Select
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex, CreateTable
from starlette.datastructures import Headers, UploadFile

from api.deps import ActiveHumanAdmin, require_tenant_admin
from api.route_enforcement import SCOPE_FAMILIES, required_scopes_for
from api.route_metadata import ROUTE_METADATA_ATTR
from api.v1 import spend as api
from core.config import settings
from core.models.spend import SpendCommitment, SpendModelAlias, SpendOrgNode
from core.models.spend_invoice import SpendInvoice, SpendInvoiceLine, SpendReconciliation, SpendReconciliationItem
from core.models.spend_usage import SpendUsageRollup
from core.ownership import Caller
from core.spend import access, fx, gate, imports, invoices, meter, pricing, rates, reconcile, vocab
from core.spend.errors import SpendError
from core.spend.pricing import FxRate
from tests.unit.spend_usage_fakes import ACTOR, TENANT, install
from tests.unit.test_spend_maintenance import fx_rate
from tests.unit.test_spend_usage import card, event

TID = str(TENANT)
IMPORTER = ACTOR
CHECKER = "44444444-4444-4444-8444-444444444444"
OCT = date(2026, 10, 1)
NOV = date(2026, 11, 1)
MONTH_END = date(2026, 10, 31)
IMPORT_AT = datetime(2026, 10, 31, 20, 0, tzinfo=UTC)  # before the month closes in UTC
RUN_AT = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)
ZERO = Decimal("0")
MIGRATION = Path("migrations/versions/v6_z82_spend_reconciliation.py")
MODELS = (SpendInvoice, SpendInvoiceLine, SpendReconciliation, SpendReconciliationItem)
CHECKER_ADMIN = ActiveHumanAdmin(user_id=uuid.UUID(CHECKER), tenant_id=TENANT, email="c@example.com", role="admin")
IMPORTER_ADMIN = ActiveHumanAdmin(user_id=uuid.UUID(IMPORTER), tenant_id=TENANT, email="i@example.com", role="admin")
AUDITOR = Caller(user_id=uuid.uuid4(), role="auditor", domains=None, is_admin=False, is_machine=False)
MACHINE = Caller(user_id=None, role="", domains=None, is_admin=False, is_machine=True)
DOMAIN_ROLE = Caller(user_id=uuid.uuid4(), role="cfo", domains=["finance"], is_admin=False, is_machine=False)


@pytest.fixture
def store(monkeypatch):
    return install(monkeypatch)


# ---------------------------------------------------------------- builders


def csv_text(lines: list[dict]) -> str:
    columns: list[str] = []
    for line in lines:
        for key in line:
            if key not in columns:
                columns.append(key)
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    for line in lines:
        writer.writerow({k: "" if v is None else v for k, v in line.items()})
    return out.getvalue()


def usage_line(amount: str = "10", **over) -> dict:
    base = {"amount": amount, "usage_type": "llm_tokens", "model_sku": "gpt-4o", "unit": "1m_input_tokens"}
    base.update(over)
    return base


async def add_invoice(
    tmp_path: Path,
    lines: list[dict],
    *,
    provider: str = "openai",
    period: str = "2026-10",
    ref: str = "INV-1",
    currency: str = "USD",
    actor: str = IMPORTER,
    replace: bool = False,
    dry_run: bool = False,
    fmt: str = "csv",
    now: datetime = IMPORT_AT,
) -> dict:
    path = tmp_path / f"{uuid.uuid4().hex}.{fmt}"
    if fmt == "json":
        path.write_text(json.dumps({"lines": lines}), encoding="utf-8")
    else:
        path.write_text(csv_text(lines), encoding="utf-8")
    return await invoices.import_invoice(
        TENANT,
        provider=provider,
        period=period,
        invoice_ref=ref,
        currency=currency,
        path=path,
        filename=path.name,
        content_type="application/json" if fmt == "json" else "text/csv",
        actor=actor,
        replace=replace,
        dry_run=dry_run,
        now=now,
    )


def rollup(store, **over) -> SpendUsageRollup:
    base = {
        "id": uuid.uuid4(),
        "tenant_id": TENANT,
        "day": OCT,
        "dims_hash": uuid.uuid4().hex * 2,
        "billing_date": OCT,
        "application": "api",
        "provider": "openai",
        "model": "gpt-4o",
        "usage_type": "llm_tokens",
        "unit": "input_token",
        "currency": "USD",
        "price_source": "contract",
        "billing_account": "tenant_key",
        "use_case": "",
        "environment": "",
        "quantity": ZERO,
        "amount": ZERO,
        "amount_inr": ZERO,
        "unconverted_amount": ZERO,
        "unpriced_quantity": ZERO,
        "overage_quantity": ZERO,
        "record_count": 1,
        "call_count": 1,
        "unpriced_count": 0,
        "unconverted_count": 0,
        "fx_estimated_count": 0,
        "overage_count": 0,
        "allocated_count": 0,
        "estimated_count": 0,
    }
    base.update(over)
    row = SpendUsageRollup(**base)
    store.add(row)
    return row


def priced_usage(store, card_row, tokens: int, *, day: date = OCT, rate: str = "83", **over) -> SpendUsageRollup:
    """A rollup group priced at the card's base price, converted to INR at ``rate``."""
    amount = Decimal(tokens) / Decimal(1_000_000) * Decimal(card_row.unit_price)
    values = {
        "rate_card_id": card_row.id,
        "quantity": Decimal(tokens),
        "amount": amount,
        "amount_inr": amount * Decimal(rate),
        "currency": card_row.currency,
        "price_source": card_row.source,
        "model": card_row.model_sku or "gpt-4o",
        "day": day,
        "billing_date": day,
    }
    values.update(over)
    return rollup(store, **values)


def group(**over) -> reconcile.MeteredGroup:
    base = {
        "billing_date": OCT,
        "usage_type": "llm_tokens",
        "model": "gpt-4o",
        "unit": "input_token",
        "currency": "USD",
        "rate_card_id": None,
        "price_source": "contract",
        "commitment_id": None,
        "quantity": Decimal("4000000"),
        "amount": Decimal("10"),
        "amount_inr": Decimal("830"),
        "unconverted_amount": ZERO,
        "unpriced_quantity": ZERO,
        "overage_quantity": ZERO,
        "records": 1,
        "unknown_account_records": 0,
    }
    base.update(over)
    return reconcile.MeteredGroup(**base)


def pcard(**over) -> pricing.Card:
    return pricing.card_from_row(card(**over))


def line(n: int = 1, **over) -> SimpleNamespace:
    base = {
        "id": uuid.uuid4(),
        "invoice_id": uuid.UUID(int=1),
        "line_no": n,
        "line_kind": "usage",
        "usage_type": "llm_tokens",
        "model_sku": "gpt-4o",
        "unit": "1m_input_tokens",
        "quantity": None,
        "amount": Decimal("10"),
        "usage_date": None,
    }
    base.update(over)
    return SimpleNamespace(**base)


USD_83 = {("USD", MONTH_END): FxRate("USD", OCT, Decimal("83"))}


def priced(groups, cards=(), *, rates=None, commitments=(), aliases=None, currency="USD", stored=None):
    return reconcile.tier_true_ups(
        reconcile.reprice(
            list(groups),
            list(cards),
            stored or {},
            list(commitments),
            USD_83 if rates is None else rates,
            aliases or {},
            provider="openai",
            invoice_currency=currency,
            b0=OCT,
            b1=NOV,
        )
    )


async def run_month(provider: str = "openai", period: str = "2026-10", *, actor: str = CHECKER, now=RUN_AT):
    return await reconcile.run(TENANT, provider=provider, period=period, actor=actor, now=now)


def usage_items(run: dict) -> list[dict]:
    return [item for item in run["items"] if item["item_kind"] == "usage"]


def audits(store, event_type: str) -> list:
    return [row for row in store.of("audit_log") if row.event_type == event_type]


def _select_sql(store, table: str) -> list[str]:
    """The PostgreSQL text of the selects the store saw that read ``table``."""
    return [
        str(s.compile(dialect=postgresql.dialect()))
        for s in store.statements
        if isinstance(s, Select) and any(getattr(f, "name", None) == table for f in s.get_final_froms())
    ]


# ---------------------------------------------------------------- import


class TestImport:
    def test_parse_period_and_refuse_bad_period(self):
        assert invoices.parse_period("2026-09") == (date(2026, 9, 1), OCT)
        assert invoices.parse_period(" 2026-12 ") == (date(2026, 12, 1), date(2027, 1, 1))
        for bad in ("2026-13", "2026-9", "2026-00", "0000-01", "9999-12", "1999-12", "", None, "2026-10-01"):
            with pytest.raises(SpendError) as info:
                invoices.parse_period(bad)
            assert info.value.status == 422 and info.value.code == "invalid_period", bad
        assert invoices.period_text(OCT) == "2026-10"

    @pytest.mark.asyncio
    async def test_invoice_import_csv_and_json_with_report(self, store, tmp_path):
        out = await add_invoice(
            tmp_path,
            [
                usage_line("10.05", quantity="4"),
                usage_line("4", unit="1m_output_tokens", quantity="0.4"),
                {"amount": "1.8", "line_kind": "tax"},
                {"amount": "-2", "line_kind": "credit"},
            ],
        )
        assert out["dry_run"] is False and out["received"] == 4 and out["created"] == 4 and out["rejected"] == []
        assert out["total_amount"] == "13.85" and out["usage_amount"] == "14.05" and out["line_count"] == 4
        invoice = store.of("spend_invoices")[0]
        assert str(invoice.id) == out["invoice_id"] and invoice.source == "csv" and invoice.status == "current"
        assert invoice.imported_by == IMPORTER and invoice.period_start == OCT and len(invoice.file_sha256) == 64
        lines = sorted(store.of("spend_invoice_lines"), key=lambda r: r.line_no)
        assert [r.line_kind for r in lines] == ["usage", "usage", "tax", "credit"]
        assert lines[0].quantity == Decimal("4") and lines[2].usage_type is None and lines[3].amount == Decimal("-2")
        entry = audits(store, "spend.invoices.import")[0]
        assert entry.details["file_sha256"] == invoice.file_sha256 and entry.details["line_count"] == 4
        assert entry.details["usage_amount"] == "14.05" and entry.actor_id == IMPORTER and entry.signature

        out = await add_invoice(
            tmp_path, [usage_line("3", usage_date="2026-10-05")], ref="ACCT-2", fmt="json", provider="Anthropic"
        )
        assert out["provider"] == "anthropic" and out["created"] == 1
        second = next(r for r in store.of("spend_invoices") if r.invoice_ref == "ACCT-2")
        assert second.source == "json"
        listed = await invoices.list_invoices(TENANT, period="2026-10")
        assert {i["invoice_ref"] for i in listed["items"]} == {"INV-1", "ACCT-2"}
        assert (await invoices.list_invoices(TENANT, provider="openai"))["items"][0]["usage_amount"] == "14.05"
        detail = await invoices.get_invoice(TENANT, invoice.id)
        assert [ln["line_no"] for ln in detail["lines"]] == [1, 2, 3, 4] and detail["period"] == "2026-10"
        assert detail["lines"][1]["unit"] == "1m_output_tokens"
        with pytest.raises(SpendError) as info:
            await invoices.get_invoice(TENANT, uuid.uuid4())
        assert info.value.status == 404

    @pytest.mark.asyncio
    async def test_invoice_import_uses_bounded_parser_and_lines_envelope(self, store, tmp_path, monkeypatch):
        rows_path = tmp_path / "rows.json"
        rows_path.write_text(json.dumps({"rows": [usage_line("1")]}), encoding="utf-8")
        common = {
            "provider": "openai", "period": "2026-10", "currency": "USD", "actor": IMPORTER, "replace": False,
            "dry_run": True, "content_type": "application/json",
        }  # fmt: skip
        out = await invoices.import_invoice(TENANT, invoice_ref="R", path=rows_path, filename="rows.json", **common)
        assert out["created"] == 1 and out["invoice_id"] is None and store.of("spend_invoices") == []
        out = await add_invoice(tmp_path, [usage_line("1"), usage_line("2")], fmt="json", dry_run=True)
        assert out["received"] == 2 and out["usage_amount"] == "3"
        seen = {}
        original = imports.parse_rows

        def spy(path, **kw):
            seen.update(kw)
            return original(path, **kw)

        monkeypatch.setattr(imports, "parse_rows", spy)
        await add_invoice(tmp_path, [usage_line("1")], dry_run=True)
        assert seen["envelope_keys"] == ("rows", "lines") and seen["required"] == ("amount",)
        monkeypatch.setattr(imports, "MAX_IMPORT_ROWS", 1)
        with pytest.raises(SpendError) as info:
            await add_invoice(tmp_path, [usage_line("1"), usage_line("2")])
        assert info.value.status == 413 and info.value.code == "too_many_rows"
        monkeypatch.setattr(imports, "MAX_IMPORT_ROWS", 5000)
        for content, code in ((b"usage_type,unit\nllm_tokens,\n", "missing_columns"), (b"amount\n", "bad_file")):
            path = tmp_path / f"{code}.csv"
            path.write_bytes(content)
            with pytest.raises(SpendError) as info:
                await invoices.import_invoice(
                    TENANT, invoice_ref="R", path=path, filename=path.name, **{**common, "content_type": "text/csv"}
                )
            assert info.value.status == 400 and info.value.code == code
        broken = tmp_path / "broken.json"
        broken.write_text('{"lines": [', encoding="utf-8")
        with pytest.raises(SpendError) as info:
            await invoices.import_invoice(TENANT, invoice_ref="R", path=broken, filename="broken.json", **common)
        assert info.value.code == "bad_file"

    @pytest.mark.asyncio
    async def test_line_kinds_and_sign_rules(self, store, tmp_path):
        out = await add_invoice(
            tmp_path,
            [
                usage_line("0"),
                usage_line("-1"),
                {"amount": "5", "line_kind": "credit"},
                {"amount": "-5", "line_kind": "credit"},
                {"amount": "-3", "line_kind": "tax"},
                {"amount": "2", "line_kind": "fee"},
                {"amount": "100", "line_kind": "commitment", "usage_type": "llm_tokens"},
                {"amount": "1", "usage_type": ""},
                usage_line("1", unit="ocr_page"),
                {"amount": "1", "line_kind": "fee", "unit": "call"},
                {"amount": "1", "line_kind": "discount"},
                usage_line("1", usage_type="tokens"),
                usage_line("1", quantity="-1"),
                usage_line("1", model_sku="=cmd"),
            ],
            dry_run=True,
        )
        assert [(r["row"], r["reason"]) for r in out["rejected"]] == [
            (3, "invalid_number"),
            (4, "invalid_number"),
            (9, "invalid_value"),
            (10, "invalid_unit"),
            (11, "invalid_unit"),
            (12, "invalid_value"),
            (13, "invalid_value"),
            (14, "invalid_number"),
            (15, "invalid_sku"),
        ]
        assert out["line_count"] == 5 and out["invoice_id"] is None
        with pytest.raises(SpendError) as info:
            await add_invoice(tmp_path, [usage_line("1"), usage_line("-1")])
        assert info.value.status == 422 and info.value.code == "invoice_rejected"
        assert info.value.extra["rejected"] == [{"row": 3, "key": "line 2", "reason": "invalid_number"}]
        assert store.of("spend_invoices") == [] and store.of("spend_invoice_lines") == []

    @pytest.mark.asyncio
    async def test_reimport_needs_replace_and_supersedes(self, store, tmp_path):
        first = await add_invoice(tmp_path, [usage_line("10")])
        with pytest.raises(SpendError) as info:
            await add_invoice(tmp_path, [usage_line("11")])
        assert info.value.status == 409 and info.value.code == "invoice_exists"
        with pytest.raises(SpendError):
            await add_invoice(tmp_path, [usage_line("11")], dry_run=True)
        preview = await add_invoice(tmp_path, [usage_line("11")], replace=True, dry_run=True)
        assert preview["superseded_id"] == first["invoice_id"] and preview["invoice_id"] is None
        second = await add_invoice(tmp_path, [usage_line("11")], replace=True)
        assert second["superseded_id"] == first["invoice_id"]
        old = next(r for r in store.of("spend_invoices") if str(r.id) == first["invoice_id"])
        assert old.status == "superseded" and str(old.superseded_by) == second["invoice_id"]
        other = await add_invoice(tmp_path, [usage_line("5")], ref="ACCT-2")
        assert len(store.of("spend_invoices")) == 3  # nothing is deleted
        lines, current = await invoices.current_lines(store, TENANT, provider="openai", period_start=OCT)
        assert {str(r.id) for r in current} == {second["invoice_id"], other["invoice_id"]}
        assert sorted(Decimal(ln.amount) for ln in lines) == [Decimal("5"), Decimal("11")]
        assert audits(store, "spend.invoices.import")[1].details["superseded_id"] == first["invoice_id"]
        assert await invoices.current_lines(store, TENANT, provider="openai", period_start=NOV) == ([], [])

    @pytest.mark.asyncio
    async def test_line_outside_period_or_wrong_currency_rejected(self, store, tmp_path):
        with pytest.raises(SpendError) as info:
            await add_invoice(
                tmp_path,
                [
                    usage_line("1", usage_date="2026-10-31"),
                    usage_line("1", usage_date="2026-11-01"),
                    usage_line("1", usage_date="2026-09-30"),
                    usage_line("1", currency="EUR"),
                    usage_line("1", currency="usd"),
                    usage_line("1", usage_date="31/10/2026"),
                ],
            )
        assert info.value.code == "invoice_rejected"
        assert [(r["row"], r["reason"]) for r in info.value.extra["rejected"]] == [
            (3, "invalid_period"),
            (4, "invalid_period"),
            (5, "currency_mismatch"),
            (7, "invalid_date"),
        ]
        assert store.of("spend_invoices") == []

    @pytest.mark.asyncio
    async def test_invoice_amount_out_of_range_is_422_not_500(self, store, tmp_path, monkeypatch):
        for amount in ("1e13", "1e100000", "NaN", "abc", "0.00000000001"):
            out = await add_invoice(tmp_path, [usage_line(amount)], dry_run=True)
            assert out["rejected"][0]["reason"] == "invalid_number", amount
        with pytest.raises(SpendError) as info:
            await add_invoice(tmp_path, [usage_line("1e12")] * 11)
        assert info.value.status == 422 and info.value.code == "invalid_number"
        for kwargs, code in (
            ({"provider": "ollama"}, "invalid_reference"),
            ({"provider": "platform_storage"}, "invalid_reference"),
            ({"ref": "=HYPERLINK()"}, "invalid_text"),
            ({"ref": "bad ref!"}, "invalid_text"),
            ({"currency": "usd1"}, "invalid_currency"),
            ({"period": "2026-13"}, "invalid_period"),
        ):
            with pytest.raises(SpendError) as info:
                await add_invoice(tmp_path, [usage_line("1")], **kwargs)
            assert info.value.status == 422 and info.value.code == code, kwargs
        # Through the route: a refused invoice answers 422 with the refused lines, never a 500.
        monkeypatch.setattr(settings, "spend_intelligence_enabled", True)
        upload = UploadFile(
            file=io.BytesIO(csv_text([usage_line("1e100000")]).encode()),
            filename="inv.csv",
            headers=Headers({"content-type": "text/csv"}),
        )
        with pytest.raises(HTTPException) as http:
            await api.import_invoice(upload, "openai", "2026-10", "INV-1", "USD", False, False, CHECKER_ADMIN, TID)
        assert http.value.status_code == 422 and http.value.detail["error"] == "invoice_rejected"
        assert http.value.detail["rejected"][0]["reason"] == "invalid_number"

    @pytest.mark.asyncio
    async def test_a_month_holds_a_bounded_number_of_current_lines(self, store, tmp_path, monkeypatch):
        # A run loads every current line of the month, so the imports bound the run.
        assert invoices.MAX_PERIOD_LINES == 4 * imports.MAX_IMPORT_ROWS
        monkeypatch.setattr(invoices, "MAX_PERIOD_LINES", 3)
        await add_invoice(tmp_path, [usage_line("1"), usage_line("2")])
        for dry_run in (True, False):
            with pytest.raises(SpendError) as info:
                await add_invoice(tmp_path, [usage_line("1"), usage_line("2")], ref="ACCT-2", dry_run=dry_run)
            assert info.value.status == 413 and info.value.code == "too_many_rows", dry_run
            assert "holds 2 current invoice lines" in info.value.message
        assert len(store.of("spend_invoices")) == 1
        await add_invoice(tmp_path, [usage_line("1")], ref="ACCT-2")  # three lines in the month
        await add_invoice(tmp_path, [usage_line("3"), usage_line("4")], replace=True)  # the replaced lines leave
        with pytest.raises(SpendError) as info:
            await add_invoice(tmp_path, [usage_line("1")] * 3, replace=True)
        assert info.value.code == "too_many_rows"
        await add_invoice(tmp_path, [usage_line("1")] * 3, period="2026-11")  # another month, its own bound
        await add_invoice(tmp_path, [usage_line("1")] * 3, provider="anthropic")  # another provider, its own bound
        assert len([r for r in store.of("spend_invoices") if r.status == "current"]) == 4


# ---------------------------------------------------------------- reconcile


class TestReconcile:
    @pytest.mark.asyncio
    async def test_only_tenant_billed_usage_is_compared_and_platform_usage_reported(self, store, tmp_path):
        c = card()
        store.add(c)
        priced_usage(store, c, 4_000_000)  # 10 USD on the tenant's key
        priced_usage(store, c, 2_000_000, billing_account="platform_key")  # 5 USD on the platform's key
        priced_usage(store, c, 400_000, billing_account=None, record_count=2)  # 1 USD, account unknown
        priced_usage(store, c, 8_000_000, billing_account="in_house")  # never compared
        await add_invoice(tmp_path, [usage_line("11")])
        run = await run_month()
        assert run["stored_amount"] == "11.0000000000" and run["repriced_amount"] == "11.0000000000"
        assert run["platform_billed_amount_inr"] == "415.0000000000" and run["unknown_account_records"] == 2
        assert run["status"] == "within_tolerance" and run["needs_review_count"] == 0
        assert usage_items(run)[0]["metered_quantity"] == "4.400000"

    @pytest.mark.asyncio
    async def test_billing_month_uses_the_provider_zone(self, store, tmp_path):
        store.add(card(provider="gemini", model_sku="gemini-1.5-pro", unit_price=Decimal("1.25")))
        fx_rate(store, OCT, "83")
        at = datetime(2026, 10, 1, 3, 0, tzinfo=UTC)  # 30 September in Pacific time, 1 October in IST and UTC
        await meter.write_events(
            store,
            TENANT,
            [event(provider="gemini", model="gemini-1.5-pro", quantity=Decimal(2_000_000), event_time=at)],
            now=at,
        )
        assert store.of("spend_usage_rollups")[0].billing_date == date(2026, 9, 30)
        await add_invoice(
            tmp_path,
            [usage_line("2.5", model_sku="gemini-1.5-pro")],
            provider="gemini",
            period="2026-09",
            now=datetime(2026, 10, 5, tzinfo=UTC),
        )
        september = await run_month("gemini", "2026-09")
        assert september["status"] == "within_tolerance" and september["billing_timezone"] == "America/Los_Angeles"
        assert september["stored_amount"] == "2.5000000000"
        await add_invoice(tmp_path, [usage_line("0", model_sku="gemini-1.5-pro")], provider="gemini")
        october = await run_month("gemini", "2026-10")
        assert october["stored_amount"] == "0.0000000000" and october["status"] == "within_tolerance"

    @pytest.mark.asyncio
    async def test_stored_and_repriced_figures_both_reported(self, store, tmp_path):
        old = card(unit_price=Decimal("2.5"), status="retired", retired_at=OCT)
        new = card(unit_price=Decimal("2.6"))
        store.add(old)
        store.add(new)
        priced_usage(store, old, 4_000_000)  # stored at 2.5 = 10; the correction prices it at 2.6 = 10.4
        await add_invoice(tmp_path, [usage_line("10.4")])
        run = await run_month()
        item = usage_items(run)[0]
        assert item["stored_amount"] == "10.0000000000" and item["repriced_amount"] == "10.4000000000"
        assert item["stored_variance_pct"] == "-3.846154" and item["repriced_variance_pct"] == "0.000000"
        assert item["status"] == "needs_review" and run["status"] == "needs_review"
        assert run["stored_variance_amount"] == "-0.4000000000" and run["repriced_variance_amount"] == "0.0000000000"
        roles = {(c["id"], c["role"]) for c in item["cards"]}
        assert roles == {(str(old.id), "stored"), (str(new.id), "repriced")}

    def test_reprice_uses_contract_card_over_list_at_run_time(self):
        contract = pcard(source="contract", unit_price=Decimal("2.5"))
        listed = pcard(source="list", unit_price=Decimal("3.0"))
        out = priced([group()], [listed, contract])
        assert out[0].repriced_amount == Decimal("10.0000000000") and out[0].repriced_card.id == contract.id
        expired = pcard(source="contract", unit_price=Decimal("2.0"), effective_to=OCT)
        assert priced([group()], [expired, listed])[0].repriced_card.id == listed.id

    @pytest.mark.asyncio
    async def test_reprice_uses_card_entered_after_metering_and_flags_it_retroactive(self, store, tmp_path):
        rollup(
            store,
            currency=None,
            price_source="none",
            quantity=Decimal(4_000_000),
            unpriced_quantity=Decimal(4_000_000),
            unpriced_count=1,
        )
        await add_invoice(tmp_path, [usage_line("10")])
        late = card(created_at=datetime(2026, 11, 1, 10, tzinfo=UTC), updated_at=datetime(2026, 11, 1, 10, tzinfo=UTC))
        store.add(late)
        run = await run_month()
        item = usage_items(run)[0]
        assert item["stored_amount"] is None and item["repriced_amount"] == "10.0000000000"
        assert item["status"] == "needs_review" and item["unpriced_quantity"] == "4.000000"
        assert run["unpriced_quantity_items"] == 1 and run["stored_amount"] is None
        assert run["retroactive"] == [
            {
                "kind": "card",
                "id": str(late.id),
                "reasons": ["after_period_end", "after_invoice_import"],
                "created_at": "2026-11-01T10:00:00+00:00",
                "updated_at": "2026-11-01T10:00:00+00:00",
            }
        ]

    def test_fallback_groups_keep_their_stored_price(self):
        stored = group(price_source="fallback_list", amount=Decimal("12.34"), amount_inr=Decimal("1024.22"))
        out = priced([stored])
        assert out[0].stored_amount == Decimal("12.34") and out[0].repriced_amount == Decimal("12.34")
        # Invoiced in INR, the kept price converts as the stored figure does.
        inr = priced([stored], currency="INR")
        assert inr[0].repriced_amount == inr[0].stored_amount == Decimal("1024.22")
        # A card entered since then prices the group instead.
        assert priced([stored], [pcard()])[0].repriced_amount == Decimal("10.0000000000")
        # In-house groups re-price at zero.
        assert priced([group(price_source="in_house", amount=ZERO)])[0].repriced_amount == ZERO

    def test_tier_true_up_continues_across_card_versions(self):
        tiers_1 = [{"from_quantity": "0", "unit_price": "2.5"}, {"from_quantity": "10", "unit_price": "2.0"}]
        tiers_2 = [{"from_quantity": "0", "unit_price": "2.4"}, {"from_quantity": "10", "unit_price": "1.9"}]
        v1 = pcard(unit_price=Decimal("2.5"), volume_tiers=tiers_1, effective_from=OCT, effective_to=date(2026, 10, 16))
        v2 = pcard(unit_price=Decimal("2.4"), volume_tiers=tiers_2, effective_from=date(2026, 10, 16))
        day_1 = group(quantity=Decimal(8_000_000), amount=Decimal("20"))
        day_2 = group(billing_date=date(2026, 10, 20), quantity=Decimal(6_000_000), amount=Decimal("14.4"))
        out = priced([day_2, day_1], [v1, v2])
        assert out[1].adjustments == ()  # 8 units below the second tier
        # Units 8 to 10 at 2.4 and 10 to 14 at 1.9 (12.4) instead of 6 at 2.4 (14.4): the ladder continues.
        assert out[0].adjustments == (
            {
                "kind": "tier_true_up",
                "contract_key": "openai|llm_tokens|gpt-4o|1m_input_tokens|contract",
                "amount": Decimal("-2.0000000000"),
            },
        )
        assert out[0].total_stored == Decimal("12.4") and out[0].total_repriced == Decimal("12.4000000000")
        items = reconcile.match([line(amount=Decimal("32.4"))], out, {}, provider="openai", days_in_month=31)
        assert items[0]["stored_amount"] == Decimal("32.4") and items[0]["status"] == "within_tolerance"
        assert len(items[0]["adjustments"]) == 1

    def test_all_units_tier_mode(self):
        tiers = [{"from_quantity": "0", "unit_price": "2.5"}, {"from_quantity": "10", "unit_price": "2.0"}]
        c = pcard(unit_price=Decimal("2.5"), volume_tiers=tiers, tier_mode="all_units")
        day_1 = group(quantity=Decimal(8_000_000), amount=Decimal("20"))
        day_2 = group(billing_date=date(2026, 10, 2), quantity=Decimal(6_000_000), amount=Decimal("15"))
        out = priced([day_1, day_2], [c])
        # The month reaches 14 units: every unit at 2.0.
        assert [p.adjustments[0]["amount"] for p in out] == [Decimal("-4.0000000000"), Decimal("-3.0000000000")]
        assert sum(p.total_repriced for p in out) == Decimal("28")
        # A batch-free card without tiers needs no true-up.
        assert priced([day_1], [pcard()])[0].adjustments == ()

    def test_commitment_overage_adjustment_uses_the_overage_rate(self):
        commitment = SimpleNamespace(
            id=uuid.uuid4(),
            kind="quantity",
            unit="1m_input_tokens",
            overage_unit_price=Decimal("3.0"),
            overage_currency="USD",
        )
        g = group(commitment_id=commitment.id, overage_quantity=Decimal(1_000_000))
        out = priced([g], [pcard()], commitments=[commitment])
        assert out[0].adjustments == (
            {"kind": "commitment_overage", "commitment_id": str(commitment.id), "amount": Decimal("0.5000000000")},
        )
        assert out[0].total_stored == Decimal("10.5") and out[0].total_repriced == Decimal("10.5")
        # An overage price in INR converts through INR: 249 INR at 83 is 3 USD.
        in_inr = SimpleNamespace(
            **{**vars(commitment), "overage_unit_price": Decimal("249"), "overage_currency": "INR"}
        )
        out = priced([g], [pcard()], commitments=[in_inr])
        assert out[0].adjustments[0]["amount"] == Decimal("0.5000000000")
        # Without the FX row the overage cannot be valued, so neither figure is known.
        out = priced([g], [pcard()], commitments=[in_inr], rates={})
        assert out[0].adjustments[0]["amount"] is None and out[0].total_repriced is None
        # A money commitment carries no overage price.
        money = SimpleNamespace(**{**vars(commitment), "kind": "money"})
        assert priced([g], [pcard()], commitments=[money])[0].adjustments == ()

    def test_currency_conversion_through_inr_marks_fx_converted(self):
        rates = {**USD_83, ("EUR", MONTH_END): FxRate("EUR", date(2026, 10, 30), Decimal("90"))}
        out = priced([group()], [pcard()], rates=rates, currency="EUR")
        assert out[0].stored_amount == Decimal("9.2222222222") and out[0].repriced_amount == Decimal("9.2222222222")
        assert out[0].fx_converted is True
        assert {(c, rate.rate_date) for c, _on, rate in out[0].fx if rate} == {
            ("EUR", date(2026, 10, 30)),
            ("USD", OCT),
        }
        inr = priced([group()], [pcard()], currency="INR")
        assert inr[0].stored_amount == Decimal("830") and inr[0].repriced_amount == Decimal("830.0000000000")
        assert inr[0].fx_converted is True
        usd = priced([group()], [pcard()])
        assert usd[0].fx_converted is False and usd[0].fx == ()
        missing = priced([group()], [pcard()], rates={}, currency="EUR")
        assert missing[0].stored_amount is None and missing[0].repriced_amount is None
        assert [(c, rate) for c, _on, rate in missing[0].fx] == [("EUR", None), ("USD", None), ("EUR", None)]

    def test_cached_tokens_match_the_cached_line_whatever_priced_them(self):
        input_card = pcard(cached_unit_price=Decimal("1.25"))
        cached = group(unit="cached_input_token", quantity=Decimal(2_000_000), amount=Decimal("2.5"))
        plain = group()
        out = priced([cached, plain], [input_card])
        assert out[0].canonical_unit == "1m_cached_input_tokens" and out[0].repriced_amount == Decimal("2.5000000000")
        items = reconcile.match(
            [line(1, amount=Decimal("10")), line(2, unit="1m_cached_input_tokens", amount=Decimal("2.5"))],
            out,
            {},
            provider="openai",
            days_in_month=31,
        )
        by_unit = {item["unit"]: item for item in items}
        assert by_unit["1m_cached_input_tokens"]["metered_quantity"] == Decimal("2.000000")
        assert by_unit["1m_cached_input_tokens"]["status"] == "within_tolerance"
        assert by_unit["1m_input_tokens"]["status"] == "within_tolerance"
        # Unsplit tokens match a 1m_tokens line; a gb_month line matches GB-days by the month's days.
        token_group = group(unit="token", quantity=Decimal(1_000_000), amount=Decimal("1"))
        storage = group(usage_type="storage", unit="gb_day", model="", quantity=Decimal("62"), amount=Decimal("6.2"))
        items = reconcile.match(
            [
                line(1, unit="1m_tokens", amount=Decimal("1")),
                line(
                    2, usage_type="storage", model_sku="", unit="gb_month", quantity=Decimal("2"), amount=Decimal("6.2")
                ),
            ],
            priced([token_group, storage], []),
            {},
            provider="openai",
            days_in_month=31,
        )
        assert items[0]["metered_quantity"] == Decimal("1.000000")
        assert items[1]["unit"] == "gb_day" and items[1]["invoice_quantity"] == Decimal("62.000000")
        assert items[1]["metered_quantity"] == Decimal("62.000000")

    @pytest.mark.asyncio
    async def test_alias_matches_the_sku_line(self, store, tmp_path):
        c = card()
        store.add(c)
        priced_usage(store, c, 4_000_000, model="gpt-4o-2024-08-06")  # metered before the alias existed
        store.add(
            SpendModelAlias(
                id=uuid.uuid4(), tenant_id=TENANT, provider="openai", alias="gpt-4o-2024-08-06", model_sku="gpt-4o"
            )
        )
        await add_invoice(tmp_path, [usage_line("10")])
        run = await run_month()
        assert len(usage_items(run)) == 1
        item = usage_items(run)[0]
        assert item["model_sku"] == "gpt-4o" and item["repriced_amount"] == "10.0000000000"
        assert item["status"] == "within_tolerance"

    def test_match_exact_then_provider_level_then_unmatched(self):
        groups = [
            group(),
            group(unit="output_token", quantity=Decimal(1_000_000), amount=Decimal("10")),
            group(model="gpt-4o-mini", quantity=Decimal(10_000_000), amount=Decimal("1.5")),
            group(usage_type="embedding_tokens", unit="embedding_token", model="emb", amount=Decimal("0.2")),
        ]
        out = priced(groups, [])  # no cards: the stored figures still match
        lines = [
            line(1, amount=Decimal("10")),  # exact: gpt-4o input
            line(2, model_sku="", unit=None, amount=Decimal("11.5")),  # every other LLM token
            line(3, usage_type="ocr_pages", model_sku="", unit="ocr_page", amount=Decimal("4")),  # nothing metered
            line(4, line_kind="tax", usage_type=None, model_sku="", unit=None, amount=Decimal("2")),
        ]
        items = reconcile.match(lines, out, {}, provider="openai", days_in_month=31)
        summary = [
            (i["item_kind"], i["usage_type"], i["model_sku"], i["unit"], i["invoice_amount"], len(i["members"]))
            for i in items
        ]
        assert summary == [
            ("usage", "llm_tokens", "gpt-4o", "1m_input_tokens", Decimal("10"), 1),
            ("usage", "ocr_pages", "", "ocr_page", Decimal("4"), 0),  # a unit before neither
            ("usage", "llm_tokens", "", None, Decimal("11.5"), 2),
            ("usage", "embedding_tokens", "emb", "1m_embedding_tokens", ZERO, 1),
            ("non_usage_line", None, "", None, Decimal("2"), 0),
        ]
        # No card: the deployment list prices (10 and 0.15 per million) re-price them, matching the stored 11.5.
        assert items[2]["stored_amount"] == Decimal("11.5") and items[2]["status"] == "within_tolerance"
        assert items[1]["stored_amount"] == ZERO and items[1]["stored_variance_pct"] == Decimal("-100")
        assert items[3]["stored_variance_pct"] is None and items[3]["status"] == "needs_review"
        assert items[4]["status"] == "informational" and items[4]["invoice_line_ids"] == [str(lines[3].id)]
        # Two lines with the same key (two accounts) are one item.
        twin = reconcile.match(
            [line(1, amount=Decimal("4")), line(2, amount=Decimal("6"), invoice_id=uuid.UUID(int=2))],
            priced([group()], [pcard()]),
            {},
            provider="openai",
            days_in_month=31,
        )
        assert len(twin) == 1 and twin[0]["invoice_amount"] == Decimal("10") and len(twin[0]["invoice_line_ids"]) == 2
        assert twin[0]["status"] == "within_tolerance"

    def test_day_grain_lines_give_day_items(self):
        day_5 = date(2026, 10, 5)
        groups = [group(billing_date=day_5), group(billing_date=date(2026, 10, 6))]
        items = reconcile.match(
            [line(1, usage_date=day_5, amount=Decimal("10")), line(2, amount=Decimal("10"))],
            priced(groups, [pcard()]),
            {},
            provider="openai",
            days_in_month=31,
        )
        assert [(i["usage_date"], len(i["members"]), i["status"]) for i in items] == [
            (day_5, 1, "within_tolerance"),
            (None, 1, "within_tolerance"),
        ]
        assert items[0]["days"] == [] and items[1]["days"][0]["date"] == "2026-10-06"

    def test_month_items_list_their_days(self):
        groups = [group(), group(billing_date=date(2026, 10, 9), quantity=Decimal(2_000_000), amount=Decimal("5"))]
        items = reconcile.match(
            [line(amount=Decimal("15"))], priced(groups, [pcard()]), {}, provider="openai", days_in_month=31
        )
        assert items[0]["days"] == [
            {
                "date": "2026-10-01",
                "metered_quantity": Decimal("4.000000"),
                "stored_amount": Decimal("10.0000000000"),
                "repriced_amount": Decimal("10.0000000000"),
            },
            {
                "date": "2026-10-09",
                "metered_quantity": Decimal("2.000000"),
                "stored_amount": Decimal("5.0000000000"),
                "repriced_amount": Decimal("5.0000000000"),
            },
        ]

    def test_within_one_percent_needs_both_figures(self):
        hundred = Decimal("100")
        assert reconcile.status_for(Decimal("101"), Decimal("101"), hundred)[0] == "within_tolerance"
        assert reconcile.status_for(Decimal("99"), Decimal("100.5"), hundred)[0] == "within_tolerance"
        out = reconcile.status_for(Decimal("101.000001"), Decimal("100"), hundred)
        assert out[0] == "needs_review" and out[1] == Decimal("1.000001")
        assert reconcile.status_for(Decimal("100"), None, hundred)[0] == "needs_review"
        assert reconcile.status_for(None, Decimal("100"), hundred)[0] == "needs_review"
        assert reconcile.status_for(Decimal("100"), Decimal("98.9"), hundred)[0] == "needs_review"
        # Shown away from zero: never better than the exact value.
        assert reconcile.pct_shown(Decimal("1.0000001")) == Decimal("1.000001")
        assert reconcile.pct_shown(Decimal("-0.4200001")) == Decimal("-0.420001")
        assert reconcile.pct_shown(Decimal("1e20")) == reconcile.PCT_LIMIT
        assert reconcile.pct_shown(Decimal("-1e20")) == -reconcile.PCT_LIMIT
        assert reconcile.pct_shown(None) is None

    @pytest.mark.asyncio
    async def test_zero_invoice_with_metered_is_needs_review(self, store, tmp_path):
        assert reconcile.status_for(Decimal("5"), Decimal("5"), ZERO) == ("needs_review", None, None)
        assert reconcile.status_for(ZERO, ZERO, ZERO) == ("within_tolerance", ZERO, ZERO)
        c = card()
        store.add(c)
        priced_usage(store, c, 4_000_000)
        await add_invoice(tmp_path, [usage_line("0")])
        run = await run_month()
        assert run["stored_variance_pct"] is None and run["repriced_variance_pct"] is None
        assert run["status"] == "needs_review" and run["stored_variance_amount"] == "10.0000000000"

    def test_unpriced_or_unconverted_group_forces_needs_review(self):
        unpriced = group(unpriced_quantity=Decimal(1_000_000))
        unconverted = group(unconverted_amount=Decimal("10"), amount_inr=ZERO)
        out = priced([unpriced, unconverted], [pcard()])
        assert out[0].stored_amount is None and out[1].stored_amount is None
        assert out[1].repriced_amount == Decimal("10.0000000000")
        for item in out:
            items = reconcile.match([line()], [item], {}, provider="openai", days_in_month=31)
            assert items[0]["status"] == "needs_review" and items[0]["stored_amount"] is None

    @pytest.mark.asyncio
    async def test_non_usage_lines_are_informational(self, store, tmp_path):
        c = card()
        store.add(c)
        priced_usage(store, c, 4_000_000)
        await add_invoice(
            tmp_path,
            [
                usage_line("10"),
                {"amount": "1.8", "line_kind": "tax"},
                {"amount": "-3", "line_kind": "credit"},
                {"amount": "50", "line_kind": "commitment", "usage_type": "llm_tokens"},
                {"amount": "0.5", "line_kind": "fee"},
            ],
        )
        run = await run_month()
        assert run["invoice_amount"] == "10.0000000000" and run["non_usage_amount"] == "49.3000000000"
        assert run["status"] == "within_tolerance" and run["item_count"] == 5
        informational = [i for i in run["items"] if i["item_kind"] == "non_usage_line"]
        assert sorted(i["line_kind"] for i in informational) == ["commitment", "credit", "fee", "tax"]
        assert {i["status"] for i in informational} == {"informational"}
        assert all(i["stored_amount"] is None and i["stored_variance_pct"] is None for i in informational)

    @pytest.mark.asyncio
    async def test_items_record_cards_and_fx_used(self, store, tmp_path):
        c = card(created_at=datetime(2026, 9, 1, tzinfo=UTC), updated_at=datetime(2026, 9, 1, tzinfo=UTC))
        store.add(c)
        fx_rate(store, OCT, "83")
        fx_rate(store, date(2026, 10, 31), "90", currency="EUR")
        priced_usage(store, c, 4_000_000)
        await add_invoice(tmp_path, [usage_line("9.2222222222")], currency="EUR")
        run = await run_month()
        item = usage_items(run)[0]
        assert item["fx_converted"] is True and item["status"] == "within_tolerance"
        assert [(c_["id"], c_["role"]) for c_ in item["cards"]] == [(str(c.id), "repriced"), (str(c.id), "stored")]
        assert item["cards"][0]["unit_price"] == "2.5" and item["cards"][0]["created_at"] == "2026-09-01T00:00:00+00:00"
        assert [(f["currency"], f["rate_date"], f["rate_to_inr"]) for f in item["fx"]] == [
            ("EUR", "2026-10-31", "90"),
            ("USD", "2026-10-01", "83"),
        ]
        assert run["card_ids"] == [str(c.id)] and {f["currency"] for f in run["fx_rows"]} == {"EUR", "USD"}
        assert run["retroactive"] == []
        stored = store.of("spend_reconciliations")[0]
        assert stored.card_ids == [c.id] and stored.fx_rows[0]["on"] == "2026-10-31"
        entry = audits(store, "spend.reconciliations.run")[0]
        assert entry.details["status"] == "within_tolerance" and entry.details["invoice_amount"] == "9.2222222222"

    @pytest.mark.asyncio
    async def test_run_refuses_without_an_invoice_or_with_mixed_currencies(self, store, tmp_path):
        with pytest.raises(SpendError) as info:
            await run_month()
        assert info.value.status == 409 and info.value.code == "invoice_missing"
        await add_invoice(tmp_path, [usage_line("1")])
        await add_invoice(tmp_path, [usage_line("1")], ref="EU-ACCT", currency="EUR")
        with pytest.raises(SpendError) as info:
            await run_month()
        assert info.value.status == 422 and info.value.code == "currency_mismatch"
        with pytest.raises(SpendError) as info:
            await run_month("vllm")
        assert info.value.code == "invalid_reference"
        with pytest.raises(SpendError) as info:
            await run_month(actor="")
        assert info.value.status == 401

    @pytest.mark.asyncio
    async def test_rerun_supersedes_and_carries_unchanged_acceptances(self, store, tmp_path):
        c = card()
        output = card(unit="1m_output_tokens", unit_price=Decimal("10"))
        store.add(c)
        store.add(output)
        priced_usage(store, c, 4_000_000)
        priced_usage(store, output, 1_000_000, unit="output_token")
        await add_invoice(tmp_path, [usage_line("12"), usage_line("10", unit="1m_output_tokens")])
        first = await run_month()
        review = next(i for i in usage_items(first) if i["status"] == "needs_review")
        accepted = await reconcile.accept_item(
            TENANT,
            uuid.UUID(first["id"]),
            uuid.UUID(review["id"]),
            reason="Provider rounding per contract",
            actor=CHECKER,
        )
        assert accepted["status"] == "accepted" and accepted["run"]["status"] == "accepted"
        second = await run_month(now=LATER)
        old = next(r for r in store.of("spend_reconciliations") if str(r.id) == first["id"])
        assert old.superseded is True and second["superseded"] is False
        carried = next(i for i in usage_items(second) if i["unit"] == "1m_input_tokens")
        assert carried["status"] == "accepted" and carried["carried_from"] == review["id"]
        assert carried["accepted_by"] == CHECKER and carried["accept_reason"] == "Provider rounding per contract"
        assert second["status"] == "accepted" and second["needs_review_count"] == 0
        assert second["accept_reason"] == "Provider rounding per contract"
        entry = audits(store, "spend.reconciliations.carry_over")[0]
        assert entry.details["items"] == [{"item_id": carried["id"], "carried_from": review["id"]}]
        listed = await reconcile.list_runs(TENANT, period="2026-10")
        assert [r["id"] for r in listed["items"]] == [second["id"]]
        everything = await reconcile.list_runs(TENANT, provider="openai", include_superseded=True)
        assert {r["id"] for r in everything["items"]} == {first["id"], second["id"]}

    @pytest.mark.asyncio
    async def test_changed_item_does_not_carry_acceptance(self, store, tmp_path):
        c = card()
        store.add(c)
        group_row = priced_usage(store, c, 4_000_000)
        await add_invoice(tmp_path, [usage_line("12")])
        first = await run_month()
        item = usage_items(first)[0]
        await reconcile.accept_item(
            TENANT, uuid.UUID(first["id"]), uuid.UUID(item["id"]), reason="Known provider rounding", actor=CHECKER
        )
        group_row.quantity = Decimal(4_400_000)  # a late record arrived
        group_row.amount = Decimal("11")
        group_row.amount_inr = Decimal("913")
        second = await run_month(now=LATER)
        assert usage_items(second)[0]["status"] == "needs_review" and usage_items(second)[0]["carried_from"] is None
        assert second["status"] == "needs_review" and audits(store, "spend.reconciliations.carry_over") == []
        # A replaced invoice has other line ids: nothing carries either.
        third_item = usage_items(second)[0]
        await reconcile.accept_item(
            TENANT,
            uuid.UUID(second["id"]),
            uuid.UUID(third_item["id"]),
            reason="Known provider rounding",
            actor=CHECKER,
        )
        await add_invoice(tmp_path, [usage_line("12")], replace=True, now=LATER)
        third = await run_month(now=LATER + timedelta(hours=1))
        assert usage_items(third)[0]["status"] == "needs_review"

    @pytest.mark.asyncio
    async def test_run_is_stale_after_a_card_fx_or_record_change(self, store, tmp_path):
        c = card(updated_at=datetime(2026, 9, 1, tzinfo=UTC))
        store.add(c)
        fx_rate(store, OCT, "83")
        fx_rate(store, date(2026, 10, 31), "90", currency="EUR")
        group_row = priced_usage(store, c, 4_000_000)
        await add_invoice(tmp_path, [usage_line("9.2222222222")], currency="EUR")
        run = await run_month()
        row = store.of("spend_reconciliations")[0]
        assert run["stale"] is False and not await reconcile.is_stale(store, TENANT, row)
        assert (await reconcile.get_run(TENANT, row.id))["stale"] is False

        c.updated_at = LATER  # a card it used changed
        assert await reconcile.is_stale(store, TENANT, row)
        c.updated_at = datetime(2026, 9, 1, tzinfo=UTC)
        other = card(model_sku="gpt-4o-mini", created_at=LATER)  # a new card of the provider for the month
        store.add(other)
        assert await reconcile.is_stale(store, TENANT, row)
        store.rows.remove(other)

        eur = next(r for r in store.of("spend_fx_rates") if str(r.currency).strip() == "EUR")
        eur.rate_to_inr = Decimal("91")  # an FX row it used changed
        assert await reconcile.is_stale(store, TENANT, row)
        eur.rate_to_inr = Decimal("90")
        fx_rate(store, date(2026, 10, 31), "84")  # a closer USD rate arrived
        assert await reconcile.is_stale(store, TENANT, row)
        store.rows.remove(
            next(
                r
                for r in store.of("spend_fx_rates")
                if r.rate_date == date(2026, 10, 31) and str(r.currency).strip() == "USD"
            )
        )
        assert not await reconcile.is_stale(store, TENANT, row)

        group_row.updated_at = LATER  # records changed (late arrival, restatement, settlement)
        assert await reconcile.is_stale(store, TENANT, row)
        group_row.updated_at = None
        await add_invoice(tmp_path, [usage_line("1")], ref="ACCT-2", currency="EUR")  # another current invoice
        assert await reconcile.is_stale(store, TENANT, row)
        row.created_at = None
        assert await reconcile.is_stale(store, TENANT, row)

    @pytest.mark.asyncio
    async def test_staleness_compares_what_the_run_read_not_only_clocks(self, store, tmp_path):
        # A writer whose transaction began before the run and committed after the run had read stamps its rows
        # earlier than the run: only a comparison of content sees that the run missed them.
        before = RUN_AT - timedelta(minutes=1)
        september = datetime(2026, 9, 1, tzinfo=UTC)
        c = card(created_at=september, updated_at=september)
        store.add(c)
        group_row = priced_usage(store, c, 4_000_000)
        await add_invoice(tmp_path, [usage_line("10")])
        run = await run_month()
        row = store.of("spend_reconciliations")[0]
        assert run["stale"] is False and set(row.input_digests) == {"usage", "cards", "aliases"}
        assert not await reconcile.is_stale(store, TENANT, row)

        group_row.quantity, group_row.amount, group_row.updated_at = Decimal(4_400_000), Decimal("11"), before
        assert await reconcile.is_stale(store, TENANT, row)  # records the run never saw, stamped before it
        group_row.quantity, group_row.amount, group_row.updated_at = Decimal(4_000_000), Decimal("10"), None
        assert not await reconcile.is_stale(store, TENANT, row)
        late = priced_usage(store, c, 1_000_000, day=date(2026, 10, 2), updated_at=before)
        assert await reconcile.is_stale(store, TENANT, row)
        store.rows.remove(late)
        platform = priced_usage(store, c, 1_000_000, billing_account="platform_key")  # the platform-billed sum
        assert await reconcile.is_stale(store, TENANT, row)
        store.rows.remove(platform)
        assert not await reconcile.is_stale(store, TENANT, row)

        c.unit_price, c.updated_at = Decimal("2.6"), before  # a card write stamped when it began
        assert await reconcile.is_stale(store, TENANT, row)
        c.unit_price, c.updated_at = Decimal("2.5"), september
        hidden = card(model_sku="gpt-4o-mini", created_at=before, updated_at=before)  # committed after the read
        store.add(hidden)
        assert await reconcile.is_stale(store, TENANT, row)
        store.rows.remove(hidden)
        assert not await reconcile.is_stale(store, TENANT, row)

        alias = SpendModelAlias(
            id=uuid.uuid4(), tenant_id=TENANT, provider="openai", alias="gpt-4o-latest", model_sku="gpt-4o",
            created_at=before, updated_at=before,
        )  # fmt: skip
        store.add(alias)
        assert await reconcile.is_stale(store, TENANT, row)  # an alias re-maps both matching and pricing
        alias.provider = "anthropic"  # another provider's alias changes nothing for this run
        assert not await reconcile.is_stale(store, TENANT, row)

        row.input_digests = {}
        assert await reconcile.is_stale(store, TENANT, row)  # a run that cannot show what it read is stale

    def test_input_digests_do_not_depend_on_how_values_were_read(self):
        stamp = datetime(2026, 9, 1, 5, 30, tzinfo=UTC)
        inputs = reconcile.Inputs(
            groups=[group(amount=Decimal("10.0000000000"), unconverted_amount=Decimal("0E-10"))],
            platform_inr=Decimal("0"),
            aliases={("openai", "a"): "gpt-4o", ("anthropic", "b"): "c"},
            cards=[pcard(created_at=stamp, updated_at=stamp)],
            stored_cards={},
        )
        same = reconcile.Inputs(
            groups=[group(amount=Decimal("10"), unconverted_amount=Decimal("0"))],
            platform_inr=Decimal("0.00"),
            aliases={("openai", "a"): "gpt-4o"},
            cards=[
                replace_card(inputs.cards[0], created_at=stamp.astimezone(timezone(timedelta(hours=5, minutes=30))))
            ],
            stored_cards={inputs.cards[0].id: inputs.cards[0]},
        )
        assert reconcile.input_digests(inputs, "openai") == reconcile.input_digests(same, "openai")
        other = reconcile.input_digests(inputs, "anthropic")
        assert other["aliases"] != reconcile.input_digests(inputs, "openai")["aliases"]
        assert all(len(value) == 64 for value in other.values())

    @pytest.mark.asyncio
    async def test_listing_reads_a_month_once_and_limits_in_sql(self, store, tmp_path, monkeypatch):
        c = card()
        store.add(c)
        priced_usage(store, c, 4_000_000)
        await add_invoice(tmp_path, [usage_line("10")])
        first = await run_month()
        second = await run_month(now=LATER)
        reads = []
        original = reconcile.load_inputs

        async def counting(session, tenant_id, provider, b0, b1):
            reads.append((provider, b0))
            return await original(session, tenant_id, provider, b0, b1)

        monkeypatch.setattr(reconcile, "load_inputs", counting)
        listed = await reconcile.list_runs(TENANT, include_superseded=True)
        assert {r["id"] for r in listed["items"]} == {first["id"], second["id"]}
        assert reads == [("openai", OCT)] and {r["stale"] for r in listed["items"]} == {False}
        monkeypatch.setattr(reconcile, "LIST_LIMIT", 1)
        store.statements.clear()
        listed = await reconcile.list_runs(TENANT, include_superseded=True)
        assert [r["id"] for r in listed["items"]] == [second["id"]]
        listing = [
            s
            for s in store.statements
            if isinstance(s, Select) and s.column_descriptions[0]["entity"] is SpendReconciliation
        ]
        assert [s._limit for s in listing] == [1]

    @pytest.mark.asyncio
    async def test_the_comparison_runs_off_the_event_loop(self, store, tmp_path, monkeypatch):
        c = card()
        store.add(c)
        priced_usage(store, c, 4_000_000)
        await add_invoice(tmp_path, [usage_line("10")])
        threaded = []
        original = asyncio.to_thread

        async def spy(func, /, *args, **kwargs):
            threaded.append(func.__name__)
            return await original(func, *args, **kwargs)

        monkeypatch.setattr(asyncio, "to_thread", spy)
        assert (await run_month())["status"] == "within_tolerance"
        assert threaded == ["_compare"]

    @pytest.mark.asyncio
    async def test_carried_acceptance_is_refused_once_the_acceptor_imported_an_invoice(self, store, tmp_path):
        c = card()
        output = card(unit="1m_output_tokens", unit_price=Decimal("10"))
        store.add(c)
        store.add(output)
        priced_usage(store, c, 4_000_000)  # 10 USD of input
        priced_usage(store, output, 1_000_000, unit="output_token")  # 10 USD of output
        await add_invoice(tmp_path, [usage_line("12")])  # imported by IMPORTER
        first = await run_month()
        review = next(i for i in usage_items(first) if i["unit"] == "1m_input_tokens")
        await reconcile.accept_item(
            TENANT, uuid.UUID(first["id"]), uuid.UUID(review["id"]), reason="Provider rounding per contract",
            actor=CHECKER,
        )  # fmt: skip
        # The acceptor then imports the month's second account; the accepted item and its line are unchanged.
        await add_invoice(tmp_path, [usage_line("10", unit="1m_output_tokens")], ref="ACCT-2", actor=CHECKER, now=LATER)
        second = await run_month(actor=IMPORTER, now=LATER + timedelta(hours=1))
        item = next(i for i in usage_items(second) if i["unit"] == "1m_input_tokens")
        assert item["invoice_line_ids"] == review["invoice_line_ids"]
        assert item["stored_amount"] == review["stored_amount"] and item["repriced_amount"] == review["repriced_amount"]
        assert item["status"] == "needs_review" and item["carried_from"] is None and item["accepted_by"] is None
        assert second["status"] == "needs_review" and audits(store, "spend.reconciliations.carry_over") == []
        # The pure rule: the same acceptance carries for anyone who imported none of the run's invoices.
        unchanged = {
            "item_kind": "usage", "status": "needs_review", "usage_type": "llm_tokens", "model_sku": "gpt-4o",
            "unit": "1m_input_tokens", "usage_date": None, "invoice_line_ids": ["a"],
            "stored_amount": Decimal("10"), "repriced_amount": Decimal("10"),
        }  # fmt: skip
        old = SimpleNamespace(
            id=uuid.uuid4(), item_kind="usage", status="accepted", usage_type="llm_tokens", model_sku="gpt-4o",
            unit="1m_input_tokens", usage_date=None, invoice_line_ids=["a"], stored_amount=Decimal("10"),
            repriced_amount=Decimal("10"), accepted_by=CHECKER, accepted_at=RUN_AT, accept_reason="agreed rounding",
        )  # fmt: skip
        assert reconcile.carry_over([dict(unchanged)], [old], refused={CHECKER}) == []
        assert len(reconcile.carry_over([dict(unchanged)], [old], refused={IMPORTER})) == 1

    @pytest.mark.asyncio
    async def test_run_loads_the_commitment_and_adds_its_overage(self, store, tmp_path):
        c = card()
        store.add(c)
        commitment = SpendCommitment(
            id=uuid.uuid4(), tenant_id=TENANT, provider="openai", usage_type="llm_tokens", model_sku="gpt-4o",
            unit="1m_input_tokens", kind="quantity", committed_quantity=Decimal("3"), period_start=OCT,
            period_end=NOV, overage_unit_price=Decimal("3.0"), overage_currency="USD",
        )  # fmt: skip
        store.add(commitment)
        priced_usage(store, c, 4_000_000, commitment_id=commitment.id, overage_quantity=Decimal(1_000_000))
        await add_invoice(tmp_path, [usage_line("10.5")])
        run = await run_month()
        item = usage_items(run)[0]
        # One million tokens over the commitment at 3.0 instead of the 2.5 already in both figures.
        assert item["adjustments"] == [
            {"kind": "commitment_overage", "commitment_id": str(commitment.id), "amount": "0.5000000000"}
        ]
        assert item["stored_amount"] == "10.5000000000" and item["repriced_amount"] == "10.5000000000"
        assert item["status"] == "within_tolerance" and run["adjustments_amount"] == "0.5000000000"

    @pytest.mark.asyncio
    async def test_fx_row_entered_after_the_month_is_flagged_retroactive(self, store, tmp_path):
        september = datetime(2026, 9, 1, tzinfo=UTC)
        store.add(card(created_at=september, updated_at=september))
        fx_rate(store, OCT, "83")
        fx_rate(store, MONTH_END, "90", currency="EUR")
        late = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
        next(r for r in store.of("spend_fx_rates") if str(r.currency).strip() == "EUR").updated_at = late
        priced_usage(store, store.of("spend_rate_cards")[0], 4_000_000)
        await add_invoice(tmp_path, [usage_line("9.2222222222")], currency="EUR")
        run = await run_month()
        assert run["retroactive"] == [
            {
                "kind": "fx",
                "currency": "EUR",
                "rate_date": "2026-10-31",
                "reasons": ["after_period_end", "after_invoice_import"],
                "updated_at": late.isoformat(),
            }
        ]

    @pytest.mark.asyncio
    async def test_a_missing_fx_rate_keeps_the_run_fresh_until_it_arrives(self, store, tmp_path):
        september = datetime(2026, 9, 1, tzinfo=UTC)
        c = card(created_at=september, updated_at=september)
        store.add(c)
        priced_usage(store, c, 4_000_000)
        await add_invoice(tmp_path, [usage_line("9.2")], currency="EUR")
        run = await run_month()
        assert run["stored_amount"] is None and run["status"] == "needs_review"
        assert {(f["currency"], f["rate_date"]) for f in run["fx_rows"]} == {("EUR", None), ("USD", None)}
        row = store.of("spend_reconciliations")[0]
        assert not await reconcile.is_stale(store, TENANT, row)  # still missing: nothing changed
        fx_rate(store, MONTH_END, "90", currency="EUR")
        assert await reconcile.is_stale(store, TENANT, row)  # it arrived: a re-run would convert
        fx_rate(store, OCT, "83")
        rerun = await run_month(now=LATER)
        fresh = next(r for r in store.of("spend_reconciliations") if str(r.id) == rerun["id"])
        assert rerun["stale"] is False and not await reconcile.is_stale(store, TENANT, fresh)
        store.rows.remove(next(r for r in store.of("spend_fx_rates") if str(r.currency).strip() == "EUR"))
        assert await reconcile.is_stale(store, TENANT, fresh)  # a row the run used is gone


# ---------------------------------------------------------------- references kept by runs


class TestReferences:
    @pytest.mark.asyncio
    async def test_a_current_run_keeps_its_cards_and_fx_rows_in_use(self, store, tmp_path):
        c = card()
        store.add(c)
        fx_rate(store, OCT, "83")
        fx_rate(store, date(2026, 10, 31), "90", currency="EUR")
        priced_usage(store, c, 4_000_000)
        await add_invoice(tmp_path, [usage_line("9.2222222222")], currency="EUR")
        assert await rates.card_in_use(store, TENANT, c.id) is None
        assert not await fx.fx_in_use(store, TENANT, "EUR", date(2026, 10, 31))
        await run_month()
        assert await rates.card_in_use(store, TENANT, c.id) == MONTH_END
        assert await fx.fx_in_use(store, TENANT, "EUR", date(2026, 10, 31))
        assert not await fx.fx_in_use(store, TENANT, "EUR", date(2026, 10, 30))
        store.of("spend_reconciliations")[0].superseded = True
        assert await rates.card_in_use(store, TENANT, c.id) is None
        assert not await fx.fx_in_use(store, TENANT, "EUR", date(2026, 10, 31))

    @pytest.mark.asyncio
    async def test_runs_are_searched_for_a_card_or_fx_row_in_sql(self, store, tmp_path):
        c = card()
        store.add(c)
        fx_rate(store, OCT, "83")
        fx_rate(store, MONTH_END, "90", currency="EUR")
        priced_usage(store, c, 4_000_000)
        await add_invoice(tmp_path, [usage_line("9.2222222222")], currency="EUR")
        await run_month()
        store.statements.clear()
        assert await rates.card_in_use(store, TENANT, c.id) == MONTH_END
        assert await rates.card_in_use(store, TENANT, uuid.uuid4()) is None
        assert await fx.fx_in_use(store, TENANT, "EUR", MONTH_END)
        assert not await fx.fx_in_use(store, TENANT, "USD", MONTH_END)  # looked up, but the row was 1 October's
        sql = _select_sql(store, "spend_reconciliations")
        assert any("= ANY (spend_reconciliations.card_ids)" in s for s in sql)
        assert any("spend_reconciliations.fx_rows @>" in s for s in sql)
        assert not any("SELECT spend_reconciliations.card_ids" in s or "SELECT spend_reconciliations.fx_rows" in s
                       for s in sql)  # fmt: skip

    def test_compared_through_caps_an_open_month_at_the_day_of_the_run(self):
        assert reconcile.compared_through("openai", OCT, None) == MONTH_END
        assert reconcile.compared_through("openai", OCT, RUN_AT) == MONTH_END  # run after the month closed
        assert reconcile.compared_through("openai", OCT, datetime(2026, 10, 10, 9, tzinfo=UTC)) == date(2026, 10, 10)
        assert reconcile.compared_through("openai", OCT, datetime(2026, 9, 20, tzinfo=UTC)) == OCT
        # Pacific billing: 03:00 UTC on 10 October is still 9 October there.
        assert reconcile.compared_through("gemini", OCT, datetime(2026, 10, 10, 3, tzinfo=UTC)) == date(2026, 10, 9)

    @pytest.mark.asyncio
    async def test_a_run_of_an_open_month_holds_its_cards_only_up_to_the_day_it_ran(self, store, tmp_path):
        open_run = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)
        c = card()
        store.add(c)
        priced_usage(store, c, 4_000_000, day=date(2026, 10, 5))  # a month-to-date export
        await add_invoice(tmp_path, [usage_line("10")], now=datetime(2026, 10, 9, tzinfo=UTC))
        await run_month(now=open_run)
        assert await rates.card_in_use(store, TENANT, c.id) == date(2026, 10, 10)
        body = {
            "provider": "openai", "usage_type": "llm_tokens", "model_sku": "gpt-4o", "unit": "1m_input_tokens",
            "unit_price": "2.4", "currency": "USD", "source": "contract", "supersede": True,
        }  # fmt: skip
        with pytest.raises(SpendError) as info:  # inside the days the run compared: a backdated change
            await rates.create_card(TENANT, {**body, "effective_from": "2026-10-08"}, actor=IMPORTER, now=open_run)
        assert info.value.status == 409 and info.value.code == "restate_required"
        assert c.effective_to is None
        # A successor from a later day touches nothing the run compared: no restatement is asked for.
        out = await rates.create_card(TENANT, {**body, "effective_from": "2026-10-20"}, actor=IMPORTER, now=open_run)
        assert out["superseded_id"] == str(c.id) and out["restate_job_id"] is None
        assert c.effective_to == date(2026, 10, 20)
        assert store.of("spend_jobs") == []


# ---------------------------------------------------------------- acceptance


async def _review_run(store, tmp_path) -> dict:
    c = card()
    store.add(c)
    priced_usage(store, c, 4_000_000)
    await add_invoice(tmp_path, [usage_line("12")])
    return await run_month()


class TestAccept:
    @pytest.mark.asyncio
    async def test_accept_requires_an_active_administrator(self, store, monkeypatch):
        accept_routes = [
            r for r in api.router.routes if r.path.startswith("/spend/reconciliations") and r.methods == {"POST"}
        ]
        assert len(accept_routes) == 3
        for route in accept_routes:
            assert require_tenant_admin in route.dependencies
            assert api.spend_admin in [d.call for d in route.dependant.dependencies]

        async def refuse(request):
            raise HTTPException(403, "An active same-tenant administrator is required")

        monkeypatch.setattr(api, "get_active_human_admin", refuse)
        with pytest.raises(HTTPException) as info:
            await api.spend_admin(object())
        assert info.value.status_code == 403
        for call in (
            reconcile.accept_item(TENANT, uuid.uuid4(), uuid.uuid4(), reason="a long enough reason", actor=""),
            reconcile.accept_run(TENANT, uuid.uuid4(), reason="a long enough reason", actor=" "),
        ):
            with pytest.raises(SpendError) as refused:
                await call
            assert refused.value.status == 401 and refused.value.code == "actor_required"

    def test_developer_and_domain_head_cannot_accept_or_run(self):
        assert SCOPE_FAMILIES["spend"] == ("audit:read", "approvals:write")
        assert required_scopes_for("spend.reconciliations.sensitive.write", "POST") == ("approvals:write",)
        from core.rbac import ROLE_SCOPES

        checker = require_tenant_admin.dependency
        for role in ("developer", "cfo", "domain_lead", "auditor"):
            request = SimpleNamespace(state=SimpleNamespace(scopes=ROLE_SCOPES[role]))
            with pytest.raises(HTTPException) as info:
                checker(request)
            assert info.value.status_code == 403, role
        checker(SimpleNamespace(state=SimpleNamespace(scopes=ROLE_SCOPES["admin"])))

    @pytest.mark.asyncio
    async def test_invoice_importer_cannot_accept(self, store, tmp_path):
        run = await _review_run(store, tmp_path)
        item = usage_items(run)[0]
        with pytest.raises(SpendError) as info:
            await reconcile.accept_item(
                TENANT, uuid.UUID(run["id"]), uuid.UUID(item["id"]), reason="Importer accepting own", actor=IMPORTER
            )
        assert info.value.status == 409 and info.value.code == "same_actor"
        assert store.of("spend_reconciliation_items")[0].status == "needs_review"

    @pytest.mark.asyncio
    async def test_accept_refused_on_superseded_run_or_foreign_item(self, store, tmp_path):
        run = await _review_run(store, tmp_path)
        item = usage_items(run)[0]
        run_id, item_id = uuid.UUID(run["id"]), uuid.UUID(item["id"])
        for rid, iid in ((uuid.uuid4(), item_id), (run_id, uuid.uuid4())):
            with pytest.raises(SpendError) as info:
                await reconcile.accept_item(TENANT, rid, iid, reason="a long enough reason", actor=CHECKER)
            assert info.value.status == 404 and info.value.code == "not_found"
        second = await run_month(now=LATER)
        with pytest.raises(SpendError) as info:  # the first run is superseded
            await reconcile.accept_item(TENANT, run_id, item_id, reason="a long enough reason", actor=CHECKER)
        assert info.value.status == 409 and info.value.code == "not_reviewable"
        with pytest.raises(SpendError) as info:  # an item of another run
            await reconcile.accept_item(
                TENANT, uuid.UUID(second["id"]), item_id, reason="a long enough reason", actor=CHECKER
            )
        assert info.value.status == 404
        new_item = uuid.UUID(usage_items(second)[0]["id"])
        await reconcile.accept_item(
            TENANT, uuid.UUID(second["id"]), new_item, reason="a long enough reason", actor=CHECKER
        )
        with pytest.raises(SpendError) as info:  # already accepted
            await reconcile.accept_item(
                TENANT, uuid.UUID(second["id"]), new_item, reason="a long enough reason", actor=CHECKER
            )
        assert info.value.code == "not_reviewable"
        with pytest.raises(SpendError) as info:
            await reconcile.accept_run(TENANT, uuid.uuid4(), reason="a long enough reason", actor=CHECKER)
        assert info.value.status == 404

    @pytest.mark.asyncio
    async def test_accept_item_requires_reason_and_is_audited(self, store, tmp_path):
        run = await _review_run(store, tmp_path)
        item = usage_items(run)[0]
        run_id, item_id = uuid.UUID(run["id"]), uuid.UUID(item["id"])
        refusals = (
            ("short", "reason_required"),
            ("  " * 20, "reason_required"),
            ("x" * 501, "reason_required"),
            ("=SUM(A1:A9) as agreed", "invalid_text"),
            ("ok\x07but a bell", "invalid_text"),
        )
        for reason, code in refusals:
            with pytest.raises(SpendError) as info:
                await reconcile.accept_item(TENANT, run_id, item_id, reason=reason, actor=CHECKER)
            assert info.value.status == 422 and info.value.code == code, reason
        out = await reconcile.accept_item(
            TENANT, run_id, item_id, reason="  Contracted rounding of 2%  ", actor=CHECKER, now=LATER
        )
        assert out["accepted_by"] == CHECKER and out["accept_reason"] == "Contracted rounding of 2%"
        assert out["accepted_at"] == LATER.isoformat() and out["run"]["needs_review_count"] == 0
        entry = audits(store, "spend.reconciliations.accept_item")[0]
        assert entry.actor_id == CHECKER and entry.details["reason"] == "Contracted rounding of 2%"
        assert entry.details["item"]["invoice_amount"] == "12.0000000000"
        assert entry.details["item"]["stored_variance_pct"] == "-16.666667"
        assert entry.details["run"]["status"] == "accepted" and entry.signature
        assert access.is_commercial_audit_event(entry.event_type)
        row = store.of("spend_reconciliations")[0]
        assert row.status == "accepted" and row.accepted_by == CHECKER and row.accepted_at == LATER

    @pytest.mark.asyncio
    async def test_accept_run_when_items_within_but_total_out(self, store, tmp_path):
        # Items within tolerance sum to a total within tolerance, so a computed run never reaches this state;
        # a run kept by an earlier rule (or a future one) still has a deliberate, audited way out.
        invoice = SpendInvoice(
            id=uuid.uuid4(), tenant_id=TENANT, provider="openai", period_start=OCT, invoice_ref="INV-1",
            currency="USD", total_amount=Decimal("100"), usage_amount=Decimal("100"), line_count=1, source="csv",
            file_sha256="0" * 64, status="current", imported_by=IMPORTER, created_at=IMPORT_AT,
        )  # fmt: skip
        store.add(invoice)
        run_row = SpendReconciliation(
            id=uuid.uuid4(), tenant_id=TENANT, provider="openai", period_start=OCT, billing_timezone="UTC",
            currency="USD", invoice_amount=Decimal("100"), stored_amount=Decimal("103"),
            repriced_amount=Decimal("103"), stored_variance_pct=Decimal("3"), repriced_variance_pct=Decimal("3"),
            status="needs_review", needs_review_count=0, invoice_ids=[str(invoice.id)], card_ids=[], fx_rows=[],
            superseded=False, run_by=CHECKER, created_at=RUN_AT,
        )  # fmt: skip
        store.add(run_row)
        item = SpendReconciliationItem(
            id=uuid.uuid4(), tenant_id=TENANT, reconciliation_id=run_row.id, item_kind="usage", line_kind="usage",
            usage_type="llm_tokens", model_sku="gpt-4o", unit="1m_input_tokens", invoice_amount=Decimal("100"),
            stored_amount=Decimal("100.5"), repriced_amount=Decimal("100.5"), status="within_tolerance",
        )  # fmt: skip
        store.add(item)
        with pytest.raises(SpendError) as info:
            await reconcile.accept_run(TENANT, run_row.id, reason="Importer cannot do this", actor=IMPORTER)
        assert info.value.code == "same_actor"
        item.status = "needs_review"
        with pytest.raises(SpendError) as info:
            await reconcile.accept_run(TENANT, run_row.id, reason="An item still needs review", actor=CHECKER)
        assert info.value.code == "not_reviewable"
        item.status = "within_tolerance"
        out = await reconcile.accept_run(TENANT, run_row.id, reason="Provider fee spread over usage", actor=CHECKER)
        assert out["status"] == "accepted" and out["accepted_by"] == CHECKER and len(out["items"]) == 1
        entry = audits(store, "spend.reconciliations.accept_run")[0]
        assert (
            entry.details["reason"] == "Provider fee spread over usage" and entry.details["stored_variance_pct"] == "3"
        )
        with pytest.raises(SpendError) as info:  # accepted once
            await reconcile.accept_run(TENANT, run_row.id, reason="Provider fee spread over usage", actor=CHECKER)
        assert info.value.code == "not_reviewable"


# ---------------------------------------------------------------- Gate 1


def node(store, kind: str, code: str) -> SpendOrgNode:
    row = SpendOrgNode(id=uuid.uuid4(), tenant_id=TENANT, code=code, name=code, kind=kind, active=True)
    store.add(row)
    return row


def attributed(store, node_row, amount_inr: str, **over) -> SpendUsageRollup:
    return rollup(
        store,
        org_node_id=node_row.id if node_row else None,
        attribution_path="application_mapping" if node_row else None,
        unattributed_reason=None if node_row else "no_mapping",
        amount_inr=Decimal(amount_inr),
        amount=Decimal(amount_inr) / 83,
        quantity=Decimal(1000),
        **over,
    )


async def _within_run(store, tmp_path, amount: str = "10") -> dict:
    c = card()
    store.add(c)
    priced_usage(store, c, int(Decimal(amount) / Decimal("2.5") * 1_000_000))
    await add_invoice(tmp_path, [usage_line(amount)])
    return await run_month()


class TestGate:
    @pytest.mark.asyncio
    async def test_gate_met_needs_98_percent_and_every_provider_within(self, store, tmp_path):
        unit = node(store, "business_unit", "BU-1")
        run = await _within_run(store, tmp_path)  # 830 INR, unattributed
        store.of("spend_usage_rollups")[0].org_node_id = unit.id
        store.of("spend_usage_rollups")[0].attribution_path = "application_mapping"
        attributed(store, None, "10", billing_account="in_house", provider="ollama")  # 10 of 840 INR unattributed
        out = await gate.status(TENANT, "2026-10", now=LATER)
        assert out["attribution"]["attributed_share"] == "0.988095" and out["attribution"]["met"] is True
        assert out["reconciliation"]["met"] is True and out["gate_met"] is True
        provider = out["reconciliation"]["providers"][0]
        assert provider["provider"] == "openai" and provider["reconciliation_id"] == run["id"]
        assert provider["invoiced"] and provider["metered"] and provider["status"] == "within_tolerance"
        assert out["period"] == "2026-10" and out["reporting_timezone"] == "Asia/Kolkata"
        assert out["reconciliation"]["tolerance_pct"] == "1.00" and out["attribution"]["target"] == "0.98"
        attributed(store, None, "20", billing_account="in_house", provider="ollama")  # now 30 of 860: 96.5%
        out = await gate.status(TENANT, "2026-10", now=LATER)
        assert out["attribution"]["reasons"] == ["below_target"] and out["gate_met"] is False

    @pytest.mark.asyncio
    async def test_gate_lists_an_invoiced_provider_without_records(self, store, tmp_path):
        await add_invoice(tmp_path, [usage_line("40", model_sku="claude-sonnet")], provider="anthropic")
        out = await gate.status(TENANT, "2026-10", now=LATER)
        entry = out["reconciliation"]["providers"][0]
        assert entry["provider"] == "anthropic" and entry["invoiced"] and not entry["metered"]
        assert out["reconciliation"]["missing_reconciliations"] == ["anthropic"]
        assert out["reconciliation"]["met"] is False
        run = await run_month("anthropic")
        assert run["status"] == "needs_review" and usage_items(run)[0]["stored_variance_pct"] == "-100.000000"
        out = await gate.status(TENANT, "2026-10", now=LATER)
        assert out["reconciliation"]["missing_reconciliations"] == [] and out["reconciliation"]["met"] is False

    @pytest.mark.asyncio
    async def test_gate_lists_missing_and_stale_reconciliations(self, store, tmp_path):
        c = card()
        store.add(c)
        group_row = priced_usage(store, c, 4_000_000)
        out = await gate.status(TENANT, "2026-10", now=LATER)
        assert out["reconciliation"]["missing_reconciliations"] == ["openai"]
        assert (
            out["reconciliation"]["providers"][0]["metered"] and not out["reconciliation"]["providers"][0]["invoiced"]
        )
        await add_invoice(tmp_path, [usage_line("10")])
        await run_month()
        assert (await gate.status(TENANT, "2026-10", now=LATER))["reconciliation"]["met"] is True
        group_row.updated_at = LATER
        out = await gate.status(TENANT, "2026-10", now=LATER)
        assert out["reconciliation"]["stale_reconciliations"] == ["openai"] and out["reconciliation"]["met"] is False
        assert out["reconciliation"]["providers"][0]["stale"] is True

    @pytest.mark.asyncio
    async def test_gate_excludes_in_house_storage_and_gpu(self, store):
        assert gate.providers_to_reconcile(
            ["openai", "ollama", "platform_storage"],
            [
                ("ollama", "llm_tokens"),
                ("platform_storage", "storage"),
                ("acme-gpu", "gpu_hours"),
                ("acme-cloud", "gpu_hours"),
                ("acme-cloud", "embedding_tokens"),
                ("tesseract", "ocr_pages"),
            ],
        ) == [("acme-cloud", False, True), ("openai", True, False)]
        rollup(store, provider="vllm", usage_type="gpu_hours", unit="gpu_node_hour", amount=Decimal("5"),
               amount_inr=Decimal("5"), currency="INR", billing_account="in_house")  # fmt: skip
        rollup(store, provider="platform_storage", usage_type="storage", unit="gb_day", amount=Decimal("1"),
               amount_inr=Decimal("1"), currency="INR", billing_account="in_house")  # fmt: skip
        rollup(store, provider="acme-gpu", usage_type="gpu_hours", unit="gpu_node_hour", amount=Decimal("1"),
               amount_inr=Decimal("1"), currency="INR", billing_account="tenant_key")  # fmt: skip
        rollup(store, provider="openai", amount=Decimal("0"), amount_inr=Decimal("0"))  # unpriced amount 0
        out = await gate.status(TENANT, "2026-10", now=LATER)
        assert out["reconciliation"]["providers"] == [] and out["reconciliation"]["met"] is True

    @pytest.mark.asyncio
    async def test_group_node_attribution_does_not_count(self, store):
        root = node(store, "group", "GRP")
        attributed(store, root, "100")
        out = await gate.status(TENANT, "2026-10", now=LATER)
        assert out["attribution"]["attributed_share"] == "0.000000" and out["attribution"]["group_share"] == "1.000000"
        assert out["attribution"]["attributed_count_share"] == "0.000000"
        assert out["attribution"]["met"] is False and "below_target" in out["attribution"]["reasons"]

    def test_unpriced_unconverted_or_fx_pending_fails_attribution(self):
        base = {"share": Decimal("0.99"), "unpriced_count": 0, "unconverted_count": 0, "fx_pending_count": 0}
        assert gate.verdict(base, [], [], [])["attribution_met"] is True
        for name, reason in (
            ("unpriced_count", "unpriced_usage"),
            ("unconverted_count", "unconverted_usage"),
            ("fx_pending_count", "fx_pending"),
        ):
            out = gate.verdict({**base, name: 1}, [], [], [])
            assert out["attribution_met"] is False and out["attribution_reasons"] == [reason]
            assert out["gate_met"] is False

    @pytest.mark.asyncio
    async def test_unpriced_records_fail_attribution_through_coverage(self, store):
        unit = node(store, "cost_centre", "CC-1")
        attributed(store, unit, "100")
        attributed(store, unit, "0", price_source="none", currency=None, unpriced_count=1,
                   unpriced_quantity=Decimal(1000))  # fmt: skip
        out = await gate.status(TENANT, "2026-10", now=LATER)
        assert out["attribution"]["attributed_share"] == "1.000000" and out["attribution"]["unpriced_count"] == 1
        assert out["attribution"]["reasons"] == ["unpriced_usage"] and out["gate_met"] is False

    @pytest.mark.asyncio
    async def test_accepted_exception_does_not_meet_gate(self, store, tmp_path):
        unit = node(store, "department", "D-1")
        run = await _review_run(store, tmp_path)
        store.of("spend_usage_rollups")[0].org_node_id = unit.id
        store.of("spend_usage_rollups")[0].attribution_path = "application_mapping"
        await reconcile.accept_item(
            TENANT,
            uuid.UUID(run["id"]),
            uuid.UUID(usage_items(run)[0]["id"]),
            reason="Accepted contractual difference",
            actor=CHECKER,
        )
        out = await gate.status(TENANT, "2026-10", now=LATER)
        assert out["attribution"]["met"] is True
        assert out["reconciliation"]["accepted_exceptions"] == ["openai"] and out["reconciliation"]["met"] is False
        assert out["gate_met"] is False

    @pytest.mark.asyncio
    async def test_gate_no_priced_usage_not_met(self, store):
        out = await gate.status(TENANT, "2026-10", now=LATER)
        assert out["attribution"]["attributed_share"] is None and out["attribution"]["reasons"] == ["no_priced_usage"]
        assert out["attribution"]["met"] is False and out["reconciliation"]["met"] is True
        assert out["gate_met"] is False and out["backfill_source"] in ("model_gateway_records", "none")
        with pytest.raises(SpendError) as info:
            await gate.status(TENANT, "2026-1", now=LATER)
        assert info.value.code == "invalid_period"

    def test_displayed_share_never_reads_as_met_when_it_is_not(self):
        def share(countable: str, amount: str) -> dict:
            return gate.attribution_of(
                {"amount_inr": amount, "countable_amount_inr": countable, "records": 3, "countable_records": 2}
            )

        exact = share("979996", "1000000")
        assert exact["share"] == Decimal("0.979996")
        from core.spend import rollups

        assert rollups.share_text(exact["share"]) == "0.979996"
        assert gate.verdict(exact | {"unpriced_count": 0}, [], [], [])["attribution_met"] is False
        almost = share("97999999", "100000000")
        assert rollups.share_text(almost["share"]) == "0.979999"  # toward zero, never "0.980000"
        assert gate.verdict(almost, [], [], [])["attribution_met"] is False
        assert gate.verdict(share("98", "100"), [], [], [])["attribution_met"] is True
        assert share("1", "0")["share"] is None and rollups.share_text(exact["count_share"]) == "0.666666"
        assert gate.attribution_of({"amount_inr": None, "records": 0})["share"] is None


# ---------------------------------------------------------------- routes, audit and the migration


def _calls():
    """One direct call of every PR D route."""
    upload = UploadFile(
        file=io.BytesIO(b"amount\n1\n"), filename="i.csv", headers=Headers({"content-type": "text/csv"})
    )
    rid, iid = uuid.uuid4(), uuid.uuid4()
    accept = api.AcceptIn(reason="a long enough reason")
    return [
        api.import_invoice(upload, "openai", "2026-10", "INV-1", "USD", False, False, CHECKER_ADMIN, TID),
        api.list_invoices(caller=AUDITOR, tenant_id=TID),
        api.get_invoice(rid, caller=AUDITOR, tenant_id=TID),
        api.run_reconciliation(api.ReconcileIn(provider="openai", period="2026-10"), CHECKER_ADMIN, tenant_id=TID),
        api.list_reconciliations(caller=AUDITOR, tenant_id=TID),
        api.get_reconciliation(rid, caller=AUDITOR, tenant_id=TID),
        api.accept_reconciliation(rid, accept, CHECKER_ADMIN, tenant_id=TID),
        api.accept_reconciliation_item(rid, iid, accept, CHECKER_ADMIN, tenant_id=TID),
        api.gate_status("2026-10", caller=AUDITOR, tenant_id=TID),
    ]


def _migration():
    spec = importlib.util.spec_from_file_location("_v6_z82_spend_reconciliation", MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Op:
    def __init__(self):
        self.sql: list[str] = []

    def execute(self, sql):
        self.sql.append(" ".join(str(sql).split()))


def _strip_ws(text: str) -> str:
    return re.sub(r"\s+", "", text)


class TestRoutesAndMigration:
    @pytest.mark.asyncio
    async def test_reconciliation_routes_not_found_while_off(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        calls = _calls()
        assert len(calls) == 9
        for call in calls:
            with pytest.raises(HTTPException) as info:
                await call
            assert info.value.status_code == 404 and info.value.detail["error"] == "spend_disabled"

    @pytest.mark.asyncio
    async def test_commercial_reads_refuse_machine_and_domain_callers(self, store):
        for caller in (MACHINE, DOMAIN_ROLE, Caller(user_id=None, role="admin", domains=None, is_admin=True,
                                                    is_machine=True)):  # fmt: skip
            for call in (
                api.list_invoices(caller=caller, tenant_id=TID),
                api.get_invoice(uuid.uuid4(), caller=caller, tenant_id=TID),
                api.list_reconciliations(caller=caller, tenant_id=TID),
                api.get_reconciliation(uuid.uuid4(), caller=caller, tenant_id=TID),
                api.gate_status("2026-10", caller=caller, tenant_id=TID),
            ):
                with pytest.raises(HTTPException) as info:
                    await call
                assert info.value.status_code == 403 and info.value.detail["error"] == "commercial_read_refused"
        commercial = {"/spend/invoices", "/spend/invoices/{invoice_id}", "/spend/reconciliations",
                      "/spend/reconciliations/{reconciliation_id}", "/spend/gate"}  # fmt: skip
        for route in api.router.routes:
            if route.methods == {"GET"} and route.path in commercial:
                assert "caller" in route.dependant.call.__code__.co_varnames, route.path

    @pytest.mark.asyncio
    async def test_route_flow_while_on(self, store, monkeypatch):
        from core.spend import clock

        monkeypatch.setattr(clock, "now_utc", lambda: RUN_AT)
        c = card()
        store.add(c)
        priced_usage(store, c, 4_000_000)
        unit = node(store, "team", "T-1")
        store.of("spend_usage_rollups")[0].org_node_id = unit.id
        store.of("spend_usage_rollups")[0].attribution_path = "application_mapping"

        def upload(body: str) -> UploadFile:
            return UploadFile(
                file=io.BytesIO(body.encode()), filename="i.csv", headers=Headers({"content-type": "text/csv"})
            )

        body = csv_text([usage_line("12"), {"amount": "2", "line_kind": "tax"}])
        preview = await api.import_invoice(upload(body), "openai", "2026-10", "INV-1", "USD", False, True,
                                           IMPORTER_ADMIN, TID)  # fmt: skip
        assert preview["dry_run"] and preview["invoice_id"] is None
        imported = await api.import_invoice(upload(body), "openai", "2026-10", "INV-1", "USD", False, False,
                                            IMPORTER_ADMIN, TID)  # fmt: skip
        with pytest.raises(HTTPException) as info:
            await api.import_invoice(upload(body), "openai", "2026-10", "INV-1", "USD", False, False,
                                     IMPORTER_ADMIN, TID)  # fmt: skip
        assert info.value.status_code == 409 and info.value.detail["error"] == "invoice_exists"
        listed = await api.list_invoices(period="2026-10", caller=AUDITOR, tenant_id=TID)
        assert [i["id"] for i in listed["items"]] == [imported["invoice_id"]]
        detail = await api.get_invoice(uuid.UUID(imported["invoice_id"]), caller=AUDITOR, tenant_id=TID)
        assert len(detail["lines"]) == 2

        run = await api.run_reconciliation(api.ReconcileIn(provider="OpenAI", period="2026-10"), CHECKER_ADMIN,
                                           tenant_id=TID)  # fmt: skip
        assert run["status"] == "needs_review" and run["created_at"] == RUN_AT.isoformat()
        assert (await api.list_reconciliations(caller=AUDITOR, tenant_id=TID))["items"][0]["stale"] is False
        got = await api.get_reconciliation(uuid.UUID(run["id"]), caller=AUDITOR, tenant_id=TID)
        assert got["id"] == run["id"] and len(got["items"]) == 2
        with pytest.raises(HTTPException) as info:
            await api.accept_reconciliation(uuid.UUID(run["id"]), api.AcceptIn(reason="a long enough reason"),
                                            CHECKER_ADMIN, tenant_id=TID)  # fmt: skip
        assert info.value.status_code == 409 and info.value.detail["error"] == "not_reviewable"
        item = usage_items(run)[0]
        with pytest.raises(HTTPException) as info:
            await api.accept_reconciliation_item(uuid.UUID(run["id"]), uuid.UUID(item["id"]),
                                                 api.AcceptIn(reason="the importer tries"), IMPORTER_ADMIN,
                                                 tenant_id=TID)  # fmt: skip
        assert info.value.status_code == 409 and info.value.detail["error"] == "same_actor"
        accepted = await api.accept_reconciliation_item(
            uuid.UUID(run["id"]), uuid.UUID(item["id"]), api.AcceptIn(reason="Contracted rounding agreed"),
            CHECKER_ADMIN, tenant_id=TID,
        )  # fmt: skip
        assert accepted["run"]["status"] == "accepted"
        status = await api.gate_status("2026-10", caller=AUDITOR, tenant_id=TID)
        assert status["attribution"]["met"] is True and status["reconciliation"]["accepted_exceptions"] == ["openai"]
        assert status["gate_met"] is False
        with pytest.raises(HTTPException) as info:
            await api.get_reconciliation(uuid.uuid4(), caller=AUDITOR, tenant_id=TID)
        assert info.value.status_code == 404
        with pytest.raises(HTTPException) as info:
            await api.run_reconciliation(api.ReconcileIn(provider="anthropic", period="2026-10"), CHECKER_ADMIN,
                                         tenant_id=TID)  # fmt: skip
        assert info.value.status_code == 409 and info.value.detail["error"] == "invoice_missing"

    @pytest.mark.asyncio
    async def test_oversize_invoice_upload_is_import_too_large(self, store, monkeypatch):
        monkeypatch.setattr(imports, "MAX_IMPORT_BYTES", 8)
        body = UploadFile(
            file=io.BytesIO(b"amount\n" * 8), filename="i.csv", headers=Headers({"content-type": "text/csv"})
        )
        with pytest.raises(HTTPException) as info:
            await api.import_invoice(body, "openai", "2026-10", "INV-1", "USD", False, False, CHECKER_ADMIN, TID)
        assert info.value.status_code == 413 and info.value.detail["error"] == "import_too_large"

    def test_route_shapes_scopes_and_bodies(self):
        from pydantic import ValidationError

        from api.main import app

        paths = set(app.openapi()["paths"])
        assert {
            "/api/v1/spend/invoices/import",
            "/api/v1/spend/invoices",
            "/api/v1/spend/invoices/{invoice_id}",
            "/api/v1/spend/reconciliations",
            "/api/v1/spend/reconciliations/{reconciliation_id}",
            "/api/v1/spend/reconciliations/{reconciliation_id}/accept",
            "/api/v1/spend/reconciliations/{reconciliation_id}/items/{item_id}/accept",
            "/api/v1/spend/gate",
        } <= paths
        routes = {(r.path, next(iter(r.methods))): r for r in api.router.routes}
        expected = {
            ("/spend/invoices/import", "POST"): ("spend.invoices.sensitive.write", "bulk-import"),
            ("/spend/invoices", "GET"): ("spend.invoices.read", "standard"),
            ("/spend/invoices/{invoice_id}", "GET"): ("spend.invoices.read", "standard"),
            ("/spend/reconciliations", "POST"): ("spend.reconciliations.sensitive.write", "bulk-import"),
            ("/spend/reconciliations", "GET"): ("spend.reconciliations.read", "standard"),
            ("/spend/reconciliations/{reconciliation_id}", "GET"): ("spend.reconciliations.read", "standard"),
            ("/spend/reconciliations/{reconciliation_id}/accept", "POST"): (
                "spend.reconciliations.sensitive.write", "standard"),
            ("/spend/reconciliations/{reconciliation_id}/items/{item_id}/accept", "POST"): (
                "spend.reconciliations.sensitive.write", "standard"),
            ("/spend/gate", "GET"): ("spend.gate.read", "standard"),
        }  # fmt: skip
        for key, (scope, rate) in expected.items():
            meta = getattr(routes[key].endpoint, ROUTE_METADATA_ATTR)
            assert meta["scope"] == scope and meta["rate_limit"] == rate, key
            assert meta["auth_required"] and meta["tenant_required"] and meta["audit_event"].startswith("spend.")
            assert (require_tenant_admin in routes[key].dependencies) == (key[1] == "POST"), key
        assert routes[("/spend/reconciliations", "POST")].status_code == 201
        for bad in ({"provider": "", "period": "2026-10"}, {"provider": "openai", "period": "2026-1"},
                    {"provider": "openai", "period": "2026-10", "extra": 1}):  # fmt: skip
            with pytest.raises(ValidationError):
                api.ReconcileIn(**bad)
        for reason in ("short", "x" * 501):
            with pytest.raises(ValidationError):
                api.AcceptIn(reason=reason)

    def test_commercial_audit_sources_cover_invoices_and_reconciliations(self):
        assert ("spend.invoices.", "SpendInvoice") in access.COMMERCIAL_AUDIT_SOURCES
        assert ("spend.reconciliations.", "SpendReconciliation") in access.COMMERCIAL_AUDIT_SOURCES
        for event_type in (
            "spend.invoices.import",
            "spend.reconciliations.run",
            "spend.reconciliations.carry_over",
            "spend.reconciliations.accept_item",
            "spend.reconciliations.accept_run",
        ):
            assert access.is_commercial_audit_event(event_type), event_type
        assert not access.is_commercial_audit_event("spend.gate.get")

    @pytest.mark.asyncio
    async def test_commercial_rows_kept_reads_every_source_table(self):
        class Capture:
            def __init__(self):
                self.statements = []

            async def execute(self, statement):
                self.statements.append(statement)
                return SimpleNamespace(scalar=lambda: True)

        capture = Capture()
        assert await access.commercial_rows_kept(capture, TENANT) is True
        sql = str(capture.statements[0].compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for table in ("spend_rate_cards", "spend_commitments", "spend_invoices", "spend_reconciliations"):
            assert f"FROM {table}" in sql, table

    def test_nothing_deletes_invoice_or_reconciliation_rows(self):
        for module in (invoices, reconcile, gate):
            source = Path(module.__file__).read_text(encoding="utf-8")
            assert "delete(" not in source and "DELETE" not in source and "session.delete" not in source

    def test_migration_v6z82_chain_rls_fk_indexes_and_partial_unique(self, monkeypatch):
        migration = _migration()
        assert migration.revision == "v6z82_spend_reconciliation" and len(migration.revision) <= 32
        assert migration.down_revision == "v6z81_spend_gpu"
        assert migration.TABLES == tuple(model.__tablename__ for model in MODELS)
        op = _Op()
        monkeypatch.setattr(migration, "op", op)
        migration.upgrade()
        for name in migration.TABLES:
            assert f"ALTER TABLE {name} ENABLE ROW LEVEL SECURITY;" in op.sql, name
            assert f"ALTER TABLE {name} FORCE ROW LEVEL SECURITY;" in op.sql, name
            assert any(s.startswith(f"CREATE POLICY {name}_tenant_isolation") for s in op.sql), name
        assert not any(s.startswith("ALTER TABLE") and "ROW LEVEL" not in s for s in op.sql)
        assert any("ux_spend_invoices_current" in s and "WHERE status = 'current'" in s for s in op.sql)
        for model in MODELS:
            table = model.__table__
            leading = [tuple(c.name for c in index.columns) for index in table.indexes]
            for fk in table.foreign_key_constraints:
                columns = tuple(c.name for c in fk.columns)
                assert columns[0] == "tenant_id" and len(columns) == 2, (table.name, columns)
                assert any(cols[: len(columns)] == columns for cols in leading), (table.name, columns)
        down = _Op()
        monkeypatch.setattr(migration, "op", down)
        migration.downgrade()
        assert down.sql == [f"DROP TABLE IF EXISTS {t};" for t in reversed(migration.TABLES)]

    def test_models_compile_to_the_migration_ddl(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        ddl = ""
        for model in MODELS:
            ddl += str(CreateTable(model.__table__).compile(dialect=postgresql.dialect()))
            for index in model.__table__.indexes:
                ddl += str(CreateIndex(index).compile(dialect=postgresql.dialect()))
        flat_ddl = _strip_ws(ddl)
        names = set(re.findall(r"CONSTRAINT (\w+)", sql)) | set(re.findall(r"INDEX IF NOT EXISTS (\w+)", sql))
        assert len(names) > 20
        for name in names:
            assert name in ddl, name
        for match in re.finditer(r"(?<!WITH )CHECK \(", sql):
            depth, start, index = 1, match.end(), match.end()
            while depth:
                depth += {"(": 1, ")": -1}.get(sql[index], 0)
                index += 1
            body = _strip_ws(sql[start : index - 1])
            assert f"CHECK({body})" in flat_ddl, body
        assert "WHERE status = 'current'" in ddl
        for model in MODELS:
            table_sql = sql[sql.index(f"CREATE TABLE IF NOT EXISTS {model.__tablename__} (") :]
            table_sql = table_sql[: table_sql.index(");")]
            for column in model.__table__.columns:
                assert re.search(rf"\b{column.name} ", table_sql), (model.__tablename__, column.name)
                if "NOT NULL DEFAULT" in table_sql.split(f"{column.name} ", 1)[1].split("\n", 1)[0]:
                    assert column.server_default is not None, (model.__tablename__, column.name)
        assert SpendReconciliation.__table__.c.card_ids.type.compile(dialect=postgresql.dialect()) == "UUID[]"
        assert str(SpendInvoice.__table__.c.currency.type) == "CHAR(3)"
        unit_check = re.search(r"unit IS NULL OR unit IN\s*\(([^)]*)\)", sql).group(1)
        assert [u.strip(" '\n") for u in unit_check.split(",")] == list(vocab.ALL_CARD_UNITS)

    def test_every_tenant_table_is_named_by_an_rls_migration(self):
        from tests.unit.test_rls_tenant_coverage import _rls_tables_declared_in_migrations

        assert {model.__tablename__ for model in MODELS} <= _rls_tables_declared_in_migrations()

    def test_closed_sets_match_the_checks(self):
        sql = MIGRATION.read_text(encoding="utf-8")
        for values in (vocab.LINE_KINDS, vocab.ITEM_KINDS, vocab.ITEM_STATUSES, vocab.RUN_STATUSES,
                       vocab.INVOICE_SOURCES, vocab.INVOICE_STATUSES):  # fmt: skip
            assert "(" + ",".join(f"'{v}'" for v in values) + ")" in sql, values

    @pytest.mark.asyncio
    async def test_status_route_lists_line_kinds(self, monkeypatch):
        monkeypatch.setattr(settings, "spend_intelligence_enabled", False)
        assert (await api.spend_status(tenant_id=TID))["line_kinds"] == list(vocab.LINE_KINDS)

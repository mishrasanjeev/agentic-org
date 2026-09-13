"""Invoice generator tests.

Covers the pure-function bits of ``core.billing.invoice_generator``:
  - _month_window: previous-month boundary math
  - _build_line_items: subscription + overage pricing logic
  - _render_pdf: golden-style assertion that the rendered PDF contains
    the expected text spans (we extract text rather than byte-compare,
    because reportlab embeds creation timestamps).

The async generate_invoices_for_period() flow is exercised separately
with mocked DB sessions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from core.billing.invoice_generator import (
    _build_line_items,
    _month_window,
    _render_pdf,
)

# ── _month_window ───────────────────────────────────────────────────


class TestMonthWindow:
    def test_mid_month_returns_full_month(self):
        ref = datetime(2026, 4, 15, 10, 0, tzinfo=UTC)
        start, end = _month_window(ref)
        assert start == datetime(2026, 4, 1, tzinfo=UTC)
        assert end == datetime(2026, 5, 1, tzinfo=UTC)

    def test_start_of_month_returns_same_month(self):
        ref = datetime(2026, 4, 1, 0, 0, tzinfo=UTC)
        start, end = _month_window(ref)
        assert start == datetime(2026, 4, 1, tzinfo=UTC)
        assert end == datetime(2026, 5, 1, tzinfo=UTC)

    def test_end_of_year_rolls_over(self):
        ref = datetime(2026, 12, 31, 23, 0, tzinfo=UTC)
        start, end = _month_window(ref)
        assert start == datetime(2026, 12, 1, tzinfo=UTC)
        assert end == datetime(2027, 1, 1, tzinfo=UTC)


# ── _build_line_items ───────────────────────────────────────────────


class TestBuildLineItems:
    def test_free_plan_zero_subtotal(self):
        items, subtotal = _build_line_items("free", task_count=500)
        assert subtotal == Decimal("0")
        # One line item — the $0 base subscription
        assert len(items) == 1
        assert items[0]["amount"] == "0"

    def test_pro_plan_under_allowance(self):
        items, subtotal = _build_line_items("pro", task_count=5_000)
        # Pro = $99 base, 10K allowance, no overage
        assert subtotal == Decimal("99.00")
        assert len(items) == 1
        assert items[0]["description"].startswith("Pro plan")

    def test_pro_plan_with_overage(self):
        items, subtotal = _build_line_items("pro", task_count=15_000)
        # Pro: $99 base + 5000 overage / 1000 * $2.50 = $99 + $12.50 = $111.50
        assert subtotal == Decimal("111.50")
        assert len(items) == 2
        # First item is base subscription
        assert items[0]["amount"] == "99.00"
        # Second item is overage line
        assert "overage" in items[1]["description"]
        assert items[1]["amount"] == "12.50"

    def test_enterprise_plan_with_huge_overage(self):
        items, subtotal = _build_line_items("enterprise", task_count=200_000)
        # Enterprise: $499 base, 100K allowance, 100K overage
        # 100000 / 1000 * 2.50 = $250
        # Total = $499 + $250 = $749
        assert subtotal == Decimal("749.00")
        assert len(items) == 2
        assert items[1]["amount"] == "250.00"

    def test_unknown_plan_returns_zero_base(self):
        """Unknown plan: $0 base + any usage charged as overage.

        Tracks current behavior — if we ever want unknown plans to
        skip billing entirely, change PLAN_MONTHLY_FEE / PLAN_TASK_ALLOWANCE
        and update this test.
        """
        items, subtotal = _build_line_items("phantom", task_count=10)
        # Base subscription line is always present
        assert items[0]["amount"] == "0"
        # 10 tasks * (2.50 / 1000) = 0.025 → quantized to 0.02
        assert subtotal == Decimal("0.02")


# ── _render_pdf golden-style ────────────────────────────────────────


class TestRenderPDF:
    @pytest.fixture
    def golden_inputs(self):
        return {
            "invoice_number": "AO-ABC123-202604",
            "tenant_name": "Acme Inc",
            "period_start": datetime(2026, 4, 1, tzinfo=UTC),
            "period_end": datetime(2026, 5, 1, tzinfo=UTC),
            "line_items": [
                {
                    "description": "Pro plan — monthly subscription",
                    "qty": 1,
                    "unit_price": "2.00",
                    "amount": "2.00",
                },
                {
                    "description": "Usage overage — 5000 tasks",
                    "qty": 5.0,
                    "unit_price": "2.50 / 1000",
                    "amount": "12.50",
                },
            ],
            "subtotal": Decimal("14.50"),
            "tax": Decimal("0.00"),
            "total": Decimal("14.50"),
            "currency": "USD",
        }

    def test_returns_pdf_bytes(self, golden_inputs):
        out = _render_pdf(**golden_inputs)
        assert isinstance(out, bytes)
        assert out.startswith(b"%PDF-")
        assert len(out) > 1000  # non-trivial size

    def test_contains_expected_strings(self, golden_inputs):
        """Golden text-extraction check.

        We avoid byte-level golden comparison because reportlab
        embeds CreationDate. Instead we extract the text streams and
        assert key fields appear.
        """
        out = _render_pdf(**golden_inputs)
        # Cheap text extraction without pulling pypdf — pdf streams
        # are usually flate-compressed but reportlab's plain text
        # spans show up in the binary for table cells.
        try:
            from io import BytesIO

            from pypdf import PdfReader

            reader = PdfReader(BytesIO(out))
            text = "".join(page.extract_text() or "" for page in reader.pages)
        except Exception:
            # Fallback — slow but robust
            text = out.decode("latin-1", errors="ignore")

        assert "AgenticOrg" in text
        assert "Invoice AO-ABC123-202604" in text
        assert "Acme Inc" in text
        assert "Pro plan" in text
        assert "14.50" in text


# ── Effective plan / provider-billed handling ───────────────────────


class _Tenant:
    def __init__(self, plan="enterprise", region="IN"):
        import uuid

        self.id = uuid.uuid4()
        self.plan = plan
        self.data_region = region


class _FakeRedis:
    def __init__(self, data):
        self._data = data

    async def get(self, key):
        return self._data.get(key)


class TestEffectivePlanAndProviderBilling:
    def test_provider_billed_skips_base_fee(self):
        items, subtotal = _build_line_items(
            "enterprise", task_count=0, currency="USD", provider_billed=True
        )
        assert items == []
        assert subtotal == Decimal("0.00")

    def test_provider_billed_still_invoices_usd_overage(self):
        items, subtotal = _build_line_items(
            "pro", task_count=12_000, currency="USD", provider_billed=True
        )
        assert len(items) == 1
        assert "overage" in items[0]["description"].lower()
        assert subtotal == Decimal("5.00")

    def test_inr_base_fee_uses_catalog_not_usd_list_price(self):
        from core.billing.catalog import plan_price_minor

        items, subtotal = _build_line_items("pro", task_count=0, currency="INR")
        assert subtotal == Decimal(plan_price_minor("pro", "INR")) / Decimal(100)
        assert items[0]["amount"] == str(subtotal)

    @pytest.mark.asyncio
    async def test_resolve_billing_state_prefers_provider_record(self, monkeypatch):
        """The billing_subscriptions row (not Redis, not Tenant.plan) decides the plan."""
        from core.billing import invoice_generator as ig
        from core.billing.subscriptions import free_subscription

        tenant = _Tenant(plan="enterprise")
        seen: list[str] = []

        async def _get_subscription(tenant_id: str):
            seen.append(tenant_id)
            sub = free_subscription(tenant_id)
            sub.update(plan="pro", tier="pro", provider="plural", status="active", is_paid=True)
            return sub

        monkeypatch.setattr("core.billing.subscriptions.get_subscription", _get_subscription)
        plan, provider = await ig._resolve_billing_state(tenant)
        assert seen == [str(tenant.id)]
        assert (plan, provider) == ("pro", "plural")
        assert ig._tenant_currency(tenant, provider) == "INR"

    @pytest.mark.asyncio
    async def test_resolve_billing_state_falls_back_to_tenant_row(self, monkeypatch):
        from core.billing import invoice_generator as ig
        from core.billing.subscriptions import free_subscription

        tenant = _Tenant(plan="free", region="US")

        async def _get_subscription(tenant_id: str):
            return free_subscription(tenant_id)

        monkeypatch.setattr("core.billing.subscriptions.get_subscription", _get_subscription)
        plan, provider = await ig._resolve_billing_state(tenant)
        assert (plan, provider) == ("free", "")
        assert ig._tenant_currency(tenant, provider) == "USD"

    @pytest.mark.asyncio
    async def test_resolve_billing_state_expired_plural_is_not_provider_billed(self, monkeypatch):
        """An expired Plural row must not suppress the base fee as 'provider billed'."""
        from core.billing import invoice_generator as ig
        from core.billing.subscriptions import free_subscription

        tenant = _Tenant(plan="free", region="IN")

        async def _get_subscription(tenant_id: str):
            sub = free_subscription(tenant_id)
            sub.update(provider="plural", status="expired", is_paid=False)
            return sub

        monkeypatch.setattr("core.billing.subscriptions.get_subscription", _get_subscription)
        plan, provider = await ig._resolve_billing_state(tenant)
        assert (plan, provider) == ("free", "")

    def test_tenant_model_default_plan_is_free(self):
        from core.models.tenant import Tenant

        col = Tenant.__table__.c.plan
        assert col.default.arg == "free"
        assert "free" in str(col.server_default.arg)

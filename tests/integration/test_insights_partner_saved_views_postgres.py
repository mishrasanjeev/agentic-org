# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The Partner view and client pack, saved views and the what-changed strip, against real Postgres.

A CA firm (tenant "Alpha") has two client companies with deadlines, filings
waiting for approval, invoices and payments. Its CFO reads the Partner view
(health after filing risk, runs, open work, outstanding balances; billing in
the period) and builds the partner client pack: one PDF per client, the
workbook and a manifest whose checksums hold; the COO may read the view but not
build the pack, and tenant "Bravo" appears nowhere. Saved views: a person's
private and shared views, who sees them, and who may change or delete them.
The strip counts what changed since the last visit and starts again once the
visit is recorded.
"""

from __future__ import annotations

import hashlib
import io
import json
import uuid
import zipfile
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from tests.integration.test_insights_isolation_postgres import World, _seed_tenant
from tests.integration.test_insights_isolation_postgres import world as world  # the shared fixture

pytestmark = pytest.mark.timeout(300)


# The domains get_allowed_domains gives each role (administrators and auditors: every domain).
DOMAINS = {"cfo": ["finance"], "coo": ["ops"], "chro": ["hr"]}


def _person(role: str, user_id: uuid.UUID | None = None, *, admin: bool = False) -> SimpleNamespace:
    scopes = ["agenticorg:admin"] if admin else ["insights:read"]
    claims = {"sub": f"{role}@example.test", "role": role, "agenticorg:user_id": str(user_id or uuid.uuid4()),
              "agenticorg:domains": DOMAINS.get(role), "grantex:scopes": scopes}
    return SimpleNamespace(state=SimpleNamespace(claims=claims, scopes=scopes, auth_mode=None), query_params={})


def _firm(db: World, tenant: SimpleNamespace) -> SimpleNamespace:
    """A second client, deadlines, filings waiting, invoices and a payment for ``tenant``."""
    from core.models.ca_client_billing import CAClientInvoice, CAClientPayment
    from core.models.company import Company
    from core.models.compliance_deadline import ComplianceDeadline
    from core.models.filing_approval import FilingApproval

    tid = uuid.UUID(tenant.tid)
    with db.admin.begin() as conn:
        conn.exec_driver_sql(f'SET search_path TO "{db.schema}"')
        first = conn.execute(text("SELECT id FROM companies WHERE tenant_id = :t"), {"t": tid}).scalar_one()
    second = uuid.uuid4()
    today = date.fromisoformat(tenant.today)
    db.add(Company(id=second, tenant_id=tid, name=f"{tenant.label} Traders", pan="BBBPB1234B", client_health_score=90))
    invoice = uuid.uuid4()
    db.add(
        ComplianceDeadline(tenant_id=tid, company_id=first, deadline_type="gstr3b", filing_period="2026-09",
                           due_date=today - timedelta(days=2)),
        ComplianceDeadline(tenant_id=tid, company_id=second, deadline_type="tds", filing_period="2026-Q2",
                           due_date=today + timedelta(days=5)),
        FilingApproval(tenant_id=tid, company_id=first, filing_type="gstr1", filing_period="2026-10",
                       status="pending", requested_by="agent"),
        FilingApproval(tenant_id=tid, company_id=first, filing_type="gstr3b", filing_period="2026-10",
                       status="approved", requested_by="agent"),
        CAClientInvoice(id=invoice, tenant_id=tid, company_id=first, invoice_number=f"{tenant.label[:2]}-001",
                        issue_date=today, due_date=today + timedelta(days=15), currency="INR",
                        subtotal=Decimal("10000"), tax=Decimal("1800"), total=Decimal("11800"),
                        amount_paid=Decimal("5000"), balance_due=Decimal("6800"), status="partially_paid",
                        line_items=[]),
        CAClientInvoice(tenant_id=tid, company_id=second, invoice_number=f"{tenant.label[:2]}-002",
                        issue_date=today, due_date=today + timedelta(days=15), currency="INR",
                        subtotal=Decimal("100"), tax=Decimal("0"), total=Decimal("100"), amount_paid=Decimal("0"),
                        balance_due=Decimal("100"), status="draft", line_items=[]),
    )
    db.add(CAClientPayment(tenant_id=tid, company_id=first, invoice_id=invoice, amount=Decimal("5000"),
                           payment_date=today, method="upi"))
    return SimpleNamespace(first=first, second=second)


def _by_key(view: dict) -> dict:
    return {m["key"]: m for m in view["tiles"] + view["charts"]}


@pytest.mark.asyncio
async def test_the_partner_view_and_client_pack(world, monkeypatch) -> None:
    from api.v1 import insights as api
    from api.v1 import insights_exports as exports_api
    from core.insights import export_jobs, packs

    monkeypatch.setattr(exports_api, "_enqueue", lambda _tid, _job: True)
    alpha, bravo = _seed_tenant(world, "Alpha"), _seed_tenant(world, "Bravo")
    ids = _firm(world, alpha)
    _firm(world, bravo)
    cfo = _person("cfo")

    view = _by_key(await api.insights_view("partner", cfo, tenant_id=alpha.tid))
    clients = view["partner.clients"]
    assert clients["value"] == 2
    rows = {row["label"]: row for row in clients["rows"]}
    # Alpha Books: 100 less 25 for the overdue deadline and 5 for the filing waiting.
    assert rows["Alpha Books"]["health"] == 70 and rows["Alpha Books"]["pending_filings"] == 1
    assert rows["Alpha Books"]["overdue_deadlines"] == 1 and rows["Alpha Books"]["outstanding"] == 6800.0
    assert rows["Alpha Traders"]["health"] == 90 and rows["Alpha Traders"]["outstanding"] == 0.0  # drafts excluded
    assert [row["label"] for row in clients["rows"]] == ["Alpha Books", "Alpha Traders"]  # least healthy first
    assert view["partner.filings"]["value"] == 1 and view["partner.filings"]["rows"] == [{"label": "gstr1", "value": 1}]
    billing = view["partner.billing"]
    assert billing["value"] == 11800.0 and billing["unit"] == "inr"
    assert billing["rows"] == [{"label": "INR", "invoiced": 11800.0, "received": 5000.0, "outstanding": 6800.0}]
    assert view["backoffice.deadlines"]["rows"] == [
        {"label": "gstr3b", "overdue": 1, "due_30d": 0},
        {"label": "tds", "overdue": 0, "due_30d": 1},
    ]
    assert "Bravo" not in json.dumps(view, default=str)

    # The COO reads the view but may not build the pack (it carries the firm's billing).
    await api.insights_view("partner", _person("coo"), tenant_id=alpha.tid)
    body = exports_api.ExportRequest(kind="partner_pack", format="zip", params={})
    with pytest.raises(HTTPException) as refused:
        await exports_api.create_export(body, _person("coo"), tenant_id=alpha.tid)
    assert refused.value.status_code == 403

    job = (await exports_api.create_export(body, cfo, tenant_id=alpha.tid))["export"]
    assert await export_jobs.build(uuid.UUID(alpha.tid), uuid.UUID(job["id"])) == "ready"
    content = (await exports_api.download_export(cfo, uuid.UUID(job["id"]), tenant_id=alpha.tid)).body
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    pdfs = sorted(name for name in files if name.startswith("clients/"))
    assert pdfs == sorted([f"clients/alpha-books-{str(ids.first)[:8]}.pdf",
                           f"clients/alpha-traders-{str(ids.second)[:8]}.pdf"])
    assert all(files[name][:5] == b"%PDF-" for name in pdfs)
    manifest = json.loads(files["manifest.json"])
    assert packs.verify_manifest(manifest) and manifest["clients"] == 2 and manifest["kind"] == "partner_pack"
    for name, entry in manifest["files"].items():
        assert hashlib.sha256(files[name]).hexdigest() == entry["sha256"], name
    from openpyxl import load_workbook

    sheets = load_workbook(io.BytesIO(files["partner.xlsx"]), read_only=True).sheetnames
    assert sheets == ["Clients", "Deadlines", "Filings", "Billing", "Filters", "Definitions"]


@pytest.mark.asyncio
async def test_saved_views_belong_to_their_owner_and_shared_ones_to_the_tenant(world) -> None:
    from api.v1 import insights_saved_views as api

    alpha, bravo = _seed_tenant(world, "Alpha"), _seed_tenant(world, "Bravo")
    admin_id, cfo_id = uuid.uuid4(), uuid.uuid4()
    admin, cfo, chro = _person("admin", admin_id, admin=True), _person("cfo", cfo_id), _person("chro")

    def body(**kw):
        return api.SavedViewIn(**{"view_key": "finance", "name": "Q3 close", "filters": {}, **kw})

    shared = (await api.create_saved_view(body(filters={"range": "custom", "from": "2026-07-01", "to": "2026-09-30",
                                                        "company_id": "dropped"}, shared=True),
                                          admin, tenant_id=alpha.tid))["saved_view"]
    assert shared["filters"] == {"range": "custom", "from": "2026-07-01", "to": "2026-09-30"}
    private = (await api.create_saved_view(body(view_key="explore:audit", name="Config changes",
                                                filters={"filter[category]": ["config"]}),
                                           admin, tenant_id=alpha.tid))["saved_view"]
    mine = (await api.create_saved_view(body(name="My month", filters={"range": "mtd"}), cfo,
                                        tenant_id=alpha.tid))["saved_view"]
    with pytest.raises(HTTPException) as clash:
        await api.create_saved_view(body(name="My month"), cfo, tenant_id=alpha.tid)
    assert clash.value.status_code == 409
    with pytest.raises(HTTPException) as cannot_open:
        await api.create_saved_view(body(view_key="explore:audit", name="x"), cfo, tenant_id=alpha.tid)
    assert cannot_open.value.status_code == 403

    listed = (await api.list_saved_views(cfo, tenant_id=alpha.tid))["saved_views"]
    assert {(v["name"], v["mine"]) for v in listed} == {("Q3 close", False), ("My month", True)}
    assert private["id"] not in {v["id"] for v in listed}  # another person's, and an explorer the CFO cannot open
    assert (await api.list_saved_views(chro, tenant_id=alpha.tid))["saved_views"] == []  # the CHRO has no finance view
    assert (await api.list_saved_views(_person("admin", admin=True), tenant_id=bravo.tid))["saved_views"] == []

    with pytest.raises(HTTPException) as not_owner:
        await api.update_saved_view(uuid.UUID(shared["id"]), api.SavedViewPatch(name="Mine now"), cfo,
                                    tenant_id=alpha.tid)
    assert not_owner.value.status_code == 403
    renamed = (await api.update_saved_view(uuid.UUID(mine["id"]), api.SavedViewPatch(name="Month to date",
                                                                                      shared=True),
                                           cfo, tenant_id=alpha.tid))["saved_view"]
    assert renamed["name"] == "Month to date" and renamed["shared"] is True
    with pytest.raises(HTTPException) as cannot_delete:
        await api.delete_saved_view(uuid.UUID(shared["id"]), cfo, tenant_id=alpha.tid)
    assert cannot_delete.value.status_code == 403
    # An administrator removes a shared view someone else saved; the other tenant cannot see it at all.
    await api.delete_saved_view(uuid.UUID(mine["id"]), admin, tenant_id=alpha.tid)
    with pytest.raises(HTTPException) as gone:
        await api.delete_saved_view(uuid.UUID(shared["id"]), _person("admin", admin=True), tenant_id=bravo.tid)
    assert gone.value.status_code == 404
    names = {v["name"] for v in (await api.list_saved_views(cfo, tenant_id=alpha.tid))["saved_views"]}
    assert names == {"Q3 close"}


@pytest.mark.asyncio
async def test_what_changed_counts_from_the_last_visit(world) -> None:
    from api.v1 import insights_saved_views as api
    from core.models.insights import RunFact

    alpha = _seed_tenant(world, "Alpha")
    admin = _person("admin", uuid.uuid4(), admin=True)

    first = await api.insights_changes(admin, tenant_id=alpha.tid)
    assert first["first_visit"] is True
    counts = {item["key"]: item["count"] for item in first["items"]}
    assert counts["agents_created"] == 2 and counts["runs_failed"] == 1 and counts["approvals_decided"] == 1
    assert counts["config_changes"] == 1  # the agent.create audit row
    # The CHRO's strip keeps to HR: no finance failure, no configuration changes.
    chro_items = {i["key"] for i in (await api.insights_changes(_person("chro"), tenant_id=alpha.tid))["items"]}
    assert "runs_failed" not in chro_items and "config_changes" not in chro_items

    await api.insights_changes_seen(admin, tenant_id=alpha.tid)
    assert (await api.insights_changes(admin, tenant_id=alpha.tid))["items"] == []
    world.add(RunFact(tenant_id=uuid.UUID(alpha.tid), run_key="agents:later:" + uuid.uuid4().hex, entrypoint="agents",
                      agent_type="ap_processor", domain="finance", status="failed",
                      started_at=datetime.now(UTC) + timedelta(seconds=1)))
    later = await api.insights_changes(admin, tenant_id=alpha.tid)
    assert later["first_visit"] is False and [(i["key"], i["count"]) for i in later["items"]] == [("runs_failed", 1)]

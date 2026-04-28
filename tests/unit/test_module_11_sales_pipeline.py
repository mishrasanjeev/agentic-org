"""Foundation #6 — Module 11 Sales Pipeline (9 TCs).

Source-pin tests for TC-SALES-001 through TC-SALES-009.

The sales pipeline is the platform's revenue surface — every
metric, score, and stage transition must be derived from real
data with no fabricated KPIs (P6.1 audit). Pipeline math also
feeds the executive dashboard, so silent regressions here
ripple up.

Pinned contracts:

- /sales/pipeline returns {funnel, total, leads}; funnel
  groups by stage; leads ordered by score DESC then
  created_at DESC.
- _lead_to_dict carries every field the UI renders — schema
  drops would silently empty UI columns.
- /sales/metrics returns total, new-this-week, funnel,
  avg_score, emails_sent_this_week, stale_leads. Each metric
  is derived from a real query (no constants, no fakes).
- Stale-lead query EXCLUDES "new" stage (a brand-new lead
  isn't "stale" yet) AND closed_won/closed_lost (those are
  done — their absence of contact isn't a stale signal).
- Due-followups query EXCLUDES closed deals.
- /sales/seed-prospects refuses outside demo/dev (production
  must NEVER auto-seed prospects via API — fabricated data is
  banned by the closure plan).
- /sales/import-csv validates upload shape BEFORE parsing
  (Session 5 TC-002/TC-005): non-CSV → 422, empty → 422,
  bad encoding → 422, missing required headers → 422.
- Lead-score color: ≥70=emerald, ≥40=amber, else slate.
  These thresholds drive the UI heat-coding the sales team
  reads at a glance.
- /sales/pipeline/process-lead requires lead_id + never
  forwards raw agent internals (Foundation #8 false-green
  prevention — leaking internals would let consumers
  sniff prompts/tool calls).
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-SALES-001 — Sales pipeline page loads
# ─────────────────────────────────────────────────────────────────


def test_tc_sales_001_pipeline_endpoint_returns_funnel_and_leads() -> None:
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    assert '@router.get("/sales/pipeline")' in src
    pipeline_block = src.split('@router.get("/sales/pipeline")', 1)[1].split(
        "@router.", 1
    )[0]
    assert '"funnel": funnel' in pipeline_block
    assert '"total": sum(funnel.values())' in pipeline_block
    assert '"leads": [_lead_to_dict(lead)' in pipeline_block


def test_tc_sales_001_lead_dict_carries_required_fields() -> None:
    """Every field the UI renders must appear in the dict.
    Schema drops would silently empty UI columns."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    for field in (
        '"id":', '"name":', '"email":', '"company":', '"role":',
        '"stage":', '"score":', '"score_factors":',
        '"assigned_agent_id":', '"last_contacted_at":',
        '"next_followup_at":', '"deal_value_usd":',
    ):
        assert field in src, f"_lead_to_dict missing key {field}"


# ─────────────────────────────────────────────────────────────────
# TC-SALES-002 — Pipeline funnel visualization
# ─────────────────────────────────────────────────────────────────


def test_tc_sales_002_funnel_groups_by_stage() -> None:
    """Funnel data is GROUP BY stage with COUNT(*). Pin the
    GROUP BY so a future refactor can't silently flatten the
    funnel into a single number."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    assert "func.count().label(\"count\")" in src
    assert "group_by(LeadPipeline.stage)" in src


# ─────────────────────────────────────────────────────────────────
# TC-SALES-003 — View lead details
# ─────────────────────────────────────────────────────────────────


def test_tc_sales_003_get_lead_endpoint_pinned() -> None:
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    assert '@router.get("/sales/pipeline/{lead_id}")' in src


def test_tc_sales_003_lead_query_is_tenant_scoped() -> None:
    """Lead lookup MUST filter by tenant_id — without it a
    user could read any tenant's lead by guessing a UUID."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    get_lead_block = src.split("async def get_lead(", 1)[1].split(
        "\n\n\n", 1
    )[0]
    assert "LeadPipeline.id == lead_id" in get_lead_block
    assert "LeadPipeline.tenant_id == tid" in get_lead_block


def test_tc_sales_003_static_routes_declared_before_lead_id_route() -> None:
    """FastAPI matches routes top-to-bottom. /sales/pipeline/
    due-followups + /sales/pipeline/process-lead MUST be
    declared BEFORE /sales/pipeline/{lead_id} or those static
    paths get swallowed as lead_ids and 404 because no UUID
    matches the literal."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    static1 = src.find('"/sales/pipeline/due-followups"')
    static2 = src.find('"/sales/pipeline/process-lead"')
    dynamic = src.find('"/sales/pipeline/{lead_id}"')
    assert static1 > 0 and static2 > 0 and dynamic > 0
    assert static1 < dynamic, (
        "due-followups route must come BEFORE the {lead_id} "
        "route or it gets swallowed"
    )
    assert static2 < dynamic, (
        "process-lead route must come BEFORE the {lead_id} "
        "route or it gets swallowed"
    )


# ─────────────────────────────────────────────────────────────────
# TC-SALES-004 — Run Sales Agent on lead
# ─────────────────────────────────────────────────────────────────


def test_tc_sales_004_process_lead_requires_lead_id() -> None:
    """Missing lead_id → 400, NOT 500 / silent ack."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    process_block = src.split("async def process_lead_with_agent(", 1)[1].split(
        "\n\n\n", 1
    )[0]
    assert 'HTTPException(400, "lead_id is required")' in process_block


def test_tc_sales_004_process_lead_returns_only_safe_fields() -> None:
    """Foundation #8 false-green prevention: the response
    surfaces ONLY status + lead_id + confidence. The raw
    agent result (tool calls, prompt, internals) is NEVER
    forwarded — leaking internals would let consumers sniff
    proprietary prompts and tool sequences."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    process_block = src.split("async def process_lead_with_agent(", 1)[1].split(
        "\n\n\n", 1
    )[0]
    assert "never forward raw agent internals" in process_block
    assert '"status": str(result.get("status", "unknown"))' in process_block
    assert '"lead_id": str(lead_id)' in process_block
    assert '"confidence": result.get("confidence")' in process_block


def test_tc_sales_004_process_lead_swaps_error_for_400() -> None:
    """If the underlying agent fails (returns {"error": ...}),
    the route returns 400 — NOT 500. The agent error is
    logged but not echoed back, again to avoid leaking
    internals to the caller."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    process_block = src.split("async def process_lead_with_agent(", 1)[1].split(
        "\n\n\n", 1
    )[0]
    assert 'if "error" in result:' in process_block
    assert 'HTTPException(400, "Sales agent processing failed")' in process_block


# ─────────────────────────────────────────────────────────────────
# TC-SALES-005 — Seed demo prospects
# ─────────────────────────────────────────────────────────────────


def test_tc_sales_005_seed_endpoint_refuses_outside_demo_dev() -> None:
    """Production MUST NEVER auto-seed prospects via API.
    Closure plan bans fabricated data from leaking into prod
    metrics. Pin the env-gated 403."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    seed_block = src.split('@router.post("/sales/seed-prospects")', 1)[1].split(
        "@router.", 1
    )[0]
    assert (
        'os.getenv("AGENTICORG_ENV", "production").lower() not in '
        '("demo", "development", "dev"):' in seed_block
    )
    assert 'HTTPException(403, "Seed prospects is only available in demo/dev environments")' in seed_block


def test_tc_sales_005_seed_idempotency_via_email_dedupe() -> None:
    """Re-seeding the same demo set must NOT create duplicate
    leads — the endpoint dedupes by email (per tenant). Pin
    the existence check so a refactor can't drop it and create
    duplicate prospect rows on every re-seed."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    seed_block = src.split('@router.post("/sales/seed-prospects")', 1)[1].split(
        "@router.", 1
    )[0]
    assert "LeadPipeline.email == prospect[\"email\"]" in seed_block
    assert "skipped.append(prospect[\"email\"])" in seed_block


# ─────────────────────────────────────────────────────────────────
# TC-SALES-006 — Import CSV leads
# ─────────────────────────────────────────────────────────────────


def test_tc_sales_006_csv_import_validates_extension() -> None:
    """Non-.csv uploads return 422 with actionable message —
    Session 5 TC-005. Without this, a JSON or binary file
    parses garbage rows + emits the misleading "Imported 0
    leads from CSV" success banner."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    csv_block = src.split('@router.post("/sales/import-csv")', 1)[1].split(
        "@router.", 1
    )[0]
    assert 'not filename.endswith(".csv"):' in csv_block
    assert '"error": "invalid_file_type"' in csv_block


def test_tc_sales_006_csv_import_validates_empty_file() -> None:
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    csv_block = src.split('@router.post("/sales/import-csv")', 1)[1].split(
        "@router.", 1
    )[0]
    assert "if not content.strip():" in csv_block
    assert '"error": "empty_file"' in csv_block


def test_tc_sales_006_csv_import_validates_encoding() -> None:
    """utf-8-sig handles BOM. Bad encoding returns 422, NOT
    500 / silent skip."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    csv_block = src.split('@router.post("/sales/import-csv")', 1)[1].split(
        "@router.", 1
    )[0]
    assert 'content.decode("utf-8-sig")' in csv_block
    assert '"error": "invalid_encoding"' in csv_block


def test_tc_sales_006_csv_import_requires_name_and_email_headers() -> None:
    """Required-header check accepts ``name`` OR ``full_name``
    aliases for the name field, plus ``email``. Pin the
    allowed aliases — a future "rename to first_name" would
    break every production CSV upload."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    csv_block = src.split('@router.post("/sales/import-csv")', 1)[1].split(
        "@router.", 1
    )[0]
    assert 'required_headers_lower = {"name", "email"}' in csv_block
    assert 'alt_name_headers = {"full_name"}' in csv_block


# ─────────────────────────────────────────────────────────────────
# TC-SALES-007 — Due follow-ups
# ─────────────────────────────────────────────────────────────────


def test_tc_sales_007_due_followups_excludes_closed_deals() -> None:
    """Closed-won / closed-lost leads MUST NOT appear in the
    due-followups list. Otherwise the sales team chases deals
    they've already won or lost."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    df_block = src.split('@router.get("/sales/pipeline/due-followups")', 1)[1].split(
        "@router.", 1
    )[0]
    assert "next_followup_at <= datetime.now(UTC)" in df_block
    assert 'LeadPipeline.stage.not_in(["closed_won", "closed_lost"])' in df_block


def test_tc_sales_007_due_followups_ordered_by_score_desc() -> None:
    """Highest-score leads first — the sales team wants the
    most-likely-to-close work at the top of the queue."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    df_block = src.split('@router.get("/sales/pipeline/due-followups")', 1)[1].split(
        "@router.", 1
    )[0]
    assert "order_by(LeadPipeline.score.desc())" in df_block


# ─────────────────────────────────────────────────────────────────
# TC-SALES-008 — Sales metrics
# ─────────────────────────────────────────────────────────────────


def test_tc_sales_008_metrics_endpoint_returns_documented_keys() -> None:
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    metrics_block = src.split('@router.get("/sales/metrics")', 1)[1].split(
        "@router.", 1
    )[0]
    for key in (
        '"total_leads"', '"new_this_week"', '"funnel"',
        '"avg_score"', '"emails_sent_this_week"', '"stale_leads"',
    ):
        assert key in metrics_block, f"metrics endpoint missing key {key}"


def test_tc_sales_008_stale_leads_excludes_new_and_closed_stages() -> None:
    """Stale = no contact in 7+ days AND not in (closed_won,
    closed_lost, NEW). Excluding 'new' is critical — a lead
    created today obviously has no prior contact, and counting
    it as stale would inflate the metric on Day 1.

    Closure plan rule: every metric must be derived from real
    data; the stage exclusion list is the contract that
    prevents bogus alerts."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    metrics_block = src.split('@router.get("/sales/metrics")', 1)[1].split(
        "@router.", 1
    )[0]
    assert (
        'LeadPipeline.stage.not_in(["closed_won", "closed_lost", "new"])'
        in metrics_block
    )


def test_tc_sales_008_avg_score_excludes_zero_scored_leads() -> None:
    """avg_score filters score > 0 — newly-imported leads
    that haven't been scored yet shouldn't drag the average
    to zero."""
    src = (REPO / "api" / "v1" / "sales.py").read_text(encoding="utf-8")
    metrics_block = src.split('@router.get("/sales/metrics")', 1)[1].split(
        "@router.", 1
    )[0]
    assert "LeadPipeline.score > 0" in metrics_block


# ─────────────────────────────────────────────────────────────────
# TC-SALES-009 — Lead score color coding
# ─────────────────────────────────────────────────────────────────


def test_tc_sales_009_score_color_thresholds_pinned() -> None:
    """Heat-coding: ≥70 = emerald (hot), ≥40 = amber (warm),
    else slate (cold). The sales team reads this color at a
    glance; shifting the thresholds silently changes their
    perception of which leads to call first."""
    src = (REPO / "ui" / "src" / "pages" / "SalesPipeline.tsx").read_text(
        encoding="utf-8"
    )
    # Tight pin on the exact ternary — both thresholds + colors
    # asserted together so a refactor can't shift one in
    # isolation.
    assert (
        'score >= 70 ? "text-emerald-600" : score >= 40 ? '
        '"text-amber-600" : "text-slate-500"'
    ) in src


def test_tc_sales_009_score_color_used_in_list_and_detail() -> None:
    """The color helper is applied in BOTH the lead list and
    the detail panel. A regression where one view used the
    helper but the other hard-coded a color would silently
    desynchronize the heat coding between views."""
    src = (REPO / "ui" / "src" / "pages" / "SalesPipeline.tsx").read_text(
        encoding="utf-8"
    )
    # scoreColor(lead.score) appears in at least 3 places (list
    # row + detail panel + agent-result).
    assert src.count("scoreColor(lead.score)") >= 2
    assert "scoreColor(agentResult.output.lead_score)" in src

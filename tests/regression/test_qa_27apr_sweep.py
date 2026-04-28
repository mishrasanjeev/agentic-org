"""Source-pin regression tests for the 2026-04-27 QA sweep.

Companion to ``ui/e2e/qa-27apr-sweep.spec.ts`` — these are the
reverse-engineered contracts that, if a future change reverts them,
would silently re-introduce the reopens documented in
``feedback_27apr_reopen_autopsy.md``.

Pinned bugs:
  - TC_001 (Aishwarya, REOPEN): create_report_schedule wraps in
    try/except + surfaces exception class in the response detail.
  - TC_004 (Aishwarya, REOPEN of yesterday): Stop button on the
    shadow Generate flow uses AbortController, not just a boolean
    ref.
  - TC_005 (Aishwarya): chat extractor handles the "text" envelope
    key + Python repr() shape + every CSS bubble has whitespace
    wrapping.
  - TC_003 (Aishwarya): SchemaEditor syncs `value` on prop change
    via useEffect.
  - Ramesh "Shadow Accuracy 40%": agents.py loads connector_configs
    from connector_ids before langgraph_run.
  - TC_002 (Aishwarya): TEI httpx client caps wait at 25s so the
    GFE 30s timeout never fires + keyword search fallback gets a
    chance.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ──────────────────────────────────────────────────────────────────
# TC_001 — create_report_schedule must surface exception class
# ──────────────────────────────────────────────────────────────────


def test_tc_001_report_schedule_surfaces_exception_class() -> None:
    """The 500 detail must include the underlying exception class.

    Pinned because the prior fix (2026-04-22) wrapped in try/except
    but returned a generic "could not create" message that hid every
    diagnostic. Aishwarya re-reported the bug because she had no
    way to tell what was failing.
    """
    src = (REPO / "api" / "v1" / "report_schedules.py").read_text(encoding="utf-8")
    # Both create + list endpoints must include the exception class.
    assert "exc_class = type(exc).__name__" in src, (
        "create_report_schedule must capture the exception class"
    )
    assert "report_schedule_create_failed" in src
    assert "report_schedule_list_failed" in src
    # The detail message must include the {exc_class}: prefix.
    assert "Could not create the report schedule ({exc_class}:" in src
    assert "Could not load report schedules ({exc_class}:" in src


def test_tc_001_report_schedules_table_self_heal_in_init_db() -> None:
    """init_db must create report_schedules + index + RLS.

    Pinned because the 27-Apr re-reopen of TC_001 had a hidden second
    layer: instrumentation surfaced the exception class but the
    underlying error was ``UndefinedTableError: relation
    "report_schedules" does not exist``. The v4.4.0 alembic migration
    was the canonical creator, but envs stamped past that revision
    (e.g. prod 2026-04-22 cutover) ended up with no table. Without
    this safety net, every fresh prod env regresses TC_001.
    """
    src = (REPO / "core" / "database.py").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS report_schedules" in src
    assert "ix_report_schedules_tenant_company" in src
    # Must be in the RLS list so cross-tenant reads are blocked.
    assert '"report_schedules",' in src


# ──────────────────────────────────────────────────────────────────
# TC_004 — Stop button uses AbortController
# ──────────────────────────────────────────────────────────────────


def test_tc_004_stop_button_uses_abort_controller() -> None:
    """Pin the AbortController plumbing so a future commit can't
    revert to flag-only stop (yesterday's shallow fix)."""
    src = (REPO / "ui" / "src" / "pages" / "AgentDetail.tsx").read_text(
        encoding="utf-8"
    )
    # AbortController ref must exist alongside the older stopRequestedRef.
    assert "abortRef = useRef<AbortController" in src
    # The Stop button onClick must call .abort() on it.
    assert "abortRef.current?.abort()" in src
    # The api.post for shadow_sample must pass the signal.
    assert "signal: abortRef.current.signal" in src
    # The catch must treat axios CanceledError as a clean stop.
    assert "CanceledError" in src
    assert "ERR_CANCELED" in src


# ──────────────────────────────────────────────────────────────────
# TC_005 — chat extractor handles the text envelope + repr()
# ──────────────────────────────────────────────────────────────────


def test_tc_005_chat_extractor_handles_text_envelope() -> None:
    """Pin the readable-keys list + Python-repr fallback in
    ChatPanel's extractReadableText."""
    src = (REPO / "ui" / "src" / "components" / "ChatPanel.tsx").read_text(
        encoding="utf-8"
    )
    # `text` must be in the readable-keys list (the LangGraph envelope).
    assert '"text"' in src
    # Other documented keys must remain.
    for key in ("answer", "response", "message", "summary", "result", "content"):
        assert f'"{key}"' in src, f"readable key {key!r} must be present"
    # Python repr fallback helper.
    assert "_pythonReprToJson" in src
    # CSS bubble overflow fix on both user + agent bubbles.
    assert "whitespace-pre-wrap break-words" in src


# ──────────────────────────────────────────────────────────────────
# TC_003 — SchemaEditor syncs value on prop change
# ──────────────────────────────────────────────────────────────────


def test_tc_003_schema_editor_syncs_value_on_prop_change() -> None:
    """SchemaEditor must have a useEffect that resets `value` when
    `initialValue` changes (the prop-derived initial)."""
    src = (REPO / "ui" / "src" / "components" / "SchemaEditor.tsx").read_text(
        encoding="utf-8"
    )
    # The sync useEffect must exist with [initialValue] as the dep.
    assert "setValue(initialValue)" in src
    assert "}, [initialValue]);" in src


# ──────────────────────────────────────────────────────────────────
# Ramesh — agents.py loads connector_configs before langgraph_run
# ──────────────────────────────────────────────────────────────────


def test_ramesh_agents_loads_connector_configs_for_shadow_runs() -> None:
    """The Generate Sample → langgraph_run path must resolve
    connector_ids → ConnectorConfig rows → decrypt → merge BEFORE
    invoking the runner. Without this every Zoho/GSTN/Tally tool
    call hits the connector with empty auth and shadow accuracy
    stays stuck at ~40%."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    # Loader function must exist.
    assert "_load_connector_configs_for_agent" in src
    # The langgraph_run call must use the resolved config, NOT the
    # vestigial top-level agent_config.get("config").
    assert "resolved_connector_config = await _load_connector_configs_for_agent" in src
    assert "connector_config=resolved_connector_config" in src
    # The loader must read encrypted credentials and decrypt them.
    assert "decrypt_for_tenant" in src
    assert "ConnectorConfig.connector_name == connector_name" in src
    # The "registry-" prefix stripping (Ramesh's TC body shows this
    # is the shape connector_ids arrive in).
    assert 'removeprefix("registry-")' in src


# ──────────────────────────────────────────────────────────────────
# TC_002 — TEI httpx client caps wait at 25s
# ──────────────────────────────────────────────────────────────────


def test_tc_002_tei_client_caps_wait_under_gfe_timeout() -> None:
    """Pin the 25s read timeout so the Google Frontend 30s gateway
    timeout never fires on a TEI cold-start. The keyword fallback
    in api/v1/knowledge.py:884 only fires if the embed call raises
    BEFORE the GFE kills the upstream request."""
    src = (REPO / "core" / "embeddings.py").read_text(encoding="utf-8")
    assert "httpx.Timeout(25.0, connect=5.0)" in src
    # The comment must explain the budget so a future contributor
    # doesn't bump it past the GFE limit thinking it's just a
    # "client wait".
    assert "Google Frontend" in src or "GFE" in src

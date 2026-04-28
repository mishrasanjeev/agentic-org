"""Foundation #6 — Module 5 Agent Creation Wizard (7 TCs).

Source-pin tests for TC-CRT-001 through TC-CRT-007. The wizard
is the primary entry point for new agents — every contract here
gates the per-step UX that customers walk through on signup.

Pinned contracts:

- 5-step flow: -1 (NL describe) → 0 persona → 1 role/type → 2
  prompt → 3 behavior/LLM → 4 review.
- Per-step ``canNext()`` gates: persona requires
  employee_name, role requires finalType, prompt requires
  text, behavior requires max_retries >= 1 + llmModel.
- ``useCustomType`` toggles between the agent_type dropdown
  and the custom text input. ``finalType`` derives from the
  toggle so downstream code doesn't branch.
- Template variable substitution happens client-side via
  ``resolvedPrompt()`` — replaces ``{{var}}`` with values from
  ``promptVars``. Empty values keep the placeholder visible
  (so the user can spot what's missing in the preview).
- Cancel button navigates back to /dashboard/agents.
- Back navigation from step 0 returns to NL describe (-1);
  from any other step decrements by 1.
- Duplicate (tenant_id, agent_type, employee_name, version)
  combination is blocked at the DB layer (UniqueConstraint
  on the Agent model).
- Shadow-fleet limit enforced (409 if exceeded) — fail-closed
  on safety errors per the Codex 2026-04-22 audit.
- LLM fallback model defaults to gemini-2.5-flash-preview-05-20
  in the wizard payload.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


# ─────────────────────────────────────────────────────────────────
# TC-CRT-001 — Full agent creation happy path
# ─────────────────────────────────────────────────────────────────


def test_tc_crt_001_wizard_starts_at_nl_describe_step() -> None:
    """The wizard initial step is -1 (NL describe). Pin the
    initial-state default so a refactor can't silently skip the
    NL step."""
    src = (REPO / "ui" / "src" / "pages" / "AgentCreate.tsx").read_text(
        encoding="utf-8"
    )
    assert "useState(-1)" in src
    assert "// -1 = NL description step" in src


def test_tc_crt_001_create_handler_posts_to_agents_endpoint() -> None:
    src = (REPO / "ui" / "src" / "pages" / "AgentCreate.tsx").read_text(
        encoding="utf-8"
    )
    assert 'api.post("/agents", {' in src
    # Required fields per the AgentCreate schema must be sent.
    create_block = src.split("async function handleCreate", 1)[1].split(
        "\n  }\n", 1
    )[0]
    for field in ('name:', 'domain,', 'agent_type:', 'system_prompt_text:',
                  'confidence_floor:', 'hitl_policy:', 'max_retries:',
                  'initial_status: "shadow"', 'llm:'):
        assert field in create_block, f"create payload missing {field}"


def test_tc_crt_001_success_navigates_to_agent_detail() -> None:
    """On success, the wizard hands off to the new agent's
    detail page so the user sees their agent immediately."""
    src = (REPO / "ui" / "src" / "pages" / "AgentCreate.tsx").read_text(
        encoding="utf-8"
    )
    assert 'navigate(`/dashboard/agents/${data.agent_id || ""}`)' in src


# ─────────────────────────────────────────────────────────────────
# TC-CRT-002 — Custom agent type
# ─────────────────────────────────────────────────────────────────


def test_tc_crt_002_use_custom_type_toggle_drives_final_type() -> None:
    """The ``useCustomType`` toggle switches between the dropdown
    and the free-text input. ``finalType`` is the derived value
    sent to the API — pin the derivation so a refactor can't
    silently send the wrong field."""
    src = (REPO / "ui" / "src" / "pages" / "AgentCreate.tsx").read_text(
        encoding="utf-8"
    )
    assert "const finalType = useCustomType ? customType : agentType;" in src
    # The create payload uses finalType, NOT agentType directly.
    assert "agent_type: finalType" in src


def test_tc_crt_002_custom_type_input_renders_when_toggled() -> None:
    """The custom text input is conditional on useCustomType.
    Pin the JSX branch so the toggle always reveals an editable
    input."""
    src = (REPO / "ui" / "src" / "pages" / "AgentCreate.tsx").read_text(
        encoding="utf-8"
    )
    assert (
        'checked={useCustomType} onChange={(e) => setUseCustomType(e.target.checked)}'
        in src
    )
    # The input renders inside `useCustomType ? (<input ...) : ...`.
    assert "useCustomType ? (" in src


# ─────────────────────────────────────────────────────────────────
# TC-CRT-003 — Template variable substitution
# ─────────────────────────────────────────────────────────────────


def test_tc_crt_003_resolved_prompt_replaces_double_brace_vars() -> None:
    """resolvedPrompt() walks promptVars and replaces ``{{key}}``
    with the value. Pin the exact pattern so a refactor can't
    silently switch to single-brace or different syntax."""
    src = (REPO / "ui" / "src" / "pages" / "AgentCreate.tsx").read_text(
        encoding="utf-8"
    )
    resolved_block = src.split("function resolvedPrompt()", 1)[1].split(
        "\n  }\n", 1
    )[0]
    assert "text.split(`{{${k}}}`).join(v" in resolved_block


def test_tc_crt_003_empty_var_keeps_placeholder_visible() -> None:
    """When a var is empty, resolvedPrompt KEEPS the ``{{var}}``
    placeholder visible (rather than blanking it). This lets
    the user spot un-filled vars in the review preview."""
    src = (REPO / "ui" / "src" / "pages" / "AgentCreate.tsx").read_text(
        encoding="utf-8"
    )
    resolved_block = src.split("function resolvedPrompt()", 1)[1].split(
        "\n  }\n", 1
    )[0]
    # The fallback expression `v || \`{{${k}}}\`` is the empty-
    # value guard.
    assert "v || `{{${k}}}`" in resolved_block


def test_tc_crt_003_resolved_prompt_used_in_create_payload_and_preview() -> None:
    """resolvedPrompt() feeds BOTH the create payload AND the
    review-step preview pane. If only one used it, the user
    would see one thing in the preview but the agent would be
    created with another — silent UX bug."""
    src = (REPO / "ui" / "src" / "pages" / "AgentCreate.tsx").read_text(
        encoding="utf-8"
    )
    assert "system_prompt_text: resolvedPrompt()" in src
    assert "resolvedPrompt().slice(0, 500)" in src


# ─────────────────────────────────────────────────────────────────
# TC-CRT-004 — Step validation
# ─────────────────────────────────────────────────────────────────


def test_tc_crt_004_can_next_gates_each_step_with_concrete_check() -> None:
    """canNext() is the gate for the Next button. Each step has
    its own required-field check; a refactor that returned true
    by default would silently let users skip past required
    fields."""
    src = (REPO / "ui" / "src" / "pages" / "AgentCreate.tsx").read_text(
        encoding="utf-8"
    )
    can_next_block = src.split("function canNext()", 1)[1].split(
        "\n  }\n", 1
    )[0]
    # Step 0 — persona requires employee_name non-empty.
    assert "step === 0" in can_next_block
    assert "employeeName.trim().length > 0" in can_next_block
    # Step 1 — role requires finalType non-empty.
    assert "step === 1" in can_next_block
    assert "finalType.trim().length > 0" in can_next_block
    # Step 2 — prompt requires non-empty text.
    assert "step === 2" in can_next_block
    assert "promptText.trim().length > 0" in can_next_block
    # Step 3 — behavior requires max_retries >= 1 AND llmModel set.
    assert "step === 3" in can_next_block
    assert "maxRetries >= 1 && llmModel.trim().length > 0" in can_next_block


# ─────────────────────────────────────────────────────────────────
# TC-CRT-005 — Cancel
# ─────────────────────────────────────────────────────────────────


def test_tc_crt_005_cancel_button_navigates_to_agents_list() -> None:
    """The wizard header has a Back button that navigates to the
    agents list. Pin the navigation target so a refactor can't
    silently send the user to a different page."""
    src = (REPO / "ui" / "src" / "pages" / "AgentCreate.tsx").read_text(
        encoding="utf-8"
    )
    assert 'navigate("/dashboard/agents")' in src


# ─────────────────────────────────────────────────────────────────
# TC-CRT-006 — Back navigation
# ─────────────────────────────────────────────────────────────────


def test_tc_crt_006_back_from_step_0_returns_to_nl_describe() -> None:
    """Back from step 0 returns to step -1 (NL describe). Without
    this, a user who clicked Back from persona would silently
    leave the wizard."""
    src = (REPO / "ui" / "src" / "pages" / "AgentCreate.tsx").read_text(
        encoding="utf-8"
    )
    # The Back-button onClick has the conditional branches.
    assert (
        "step > 0 ? setStep(step - 1) : step === 0 ? setStep(-1) : "
        'navigate("/dashboard/agents")' in src
    )


def test_tc_crt_006_back_button_label_changes_at_step_0() -> None:
    """The Back button label switches from "Back" to "Back to
    Describe" at step 0 so the user knows where they're going."""
    src = (REPO / "ui" / "src" / "pages" / "AgentCreate.tsx").read_text(
        encoding="utf-8"
    )
    assert 'step === 0 ? "Back to Describe" : "Back"' in src


# ─────────────────────────────────────────────────────────────────
# TC-CRT-007 — Duplicate name + type
# ─────────────────────────────────────────────────────────────────


def test_tc_crt_007_agent_model_unique_constraint_pinned() -> None:
    """The DB layer enforces uniqueness on
    (tenant_id, agent_type, employee_name, version). A duplicate
    create raises an IntegrityError at insert time. Pin the
    constraint members exactly — silent removal would let
    duplicates accumulate."""
    src = (REPO / "core" / "models" / "agent.py").read_text(encoding="utf-8")
    assert (
        'UniqueConstraint("tenant_id", "agent_type", "employee_name", "version")'
        in src
    )


def test_tc_crt_007_create_handler_surfaces_create_errors_to_ui() -> None:
    """When the API returns 4xx/5xx (e.g. duplicate-error 409),
    the wizard catches it and surfaces the message via setError
    — does NOT silently navigate away or show a misleading
    success."""
    src = (REPO / "ui" / "src" / "pages" / "AgentCreate.tsx").read_text(
        encoding="utf-8"
    )
    create_block = src.split("async function handleCreate", 1)[1].split(
        "} finally {", 1
    )[0]
    assert 'setError(extractApiError(e,' in create_block
    assert '"Failed to create agent. Please try again."' in create_block


# ─────────────────────────────────────────────────────────────────
# Cross-pin — shadow-fleet limit (Codex 2026-04-22 fail-closed)
# ─────────────────────────────────────────────────────────────────


def test_create_agent_enforces_shadow_fleet_limit() -> None:
    """When the tenant has hit max_shadow_agents, create returns
    409 with the count + limit. The Codex audit closed gap #7:
    a DB hiccup must NOT bypass the limit silently — wrap raises
    in fail-closed 503, never degrade-to-weaker-state."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert "max_shadow_agents" in src
    assert "Shadow limit reached" in src
    assert (
        "Could not verify the tenant's shadow-agent budget" in src
    )


def test_create_agent_validates_authorized_tools_against_registry() -> None:
    """Caller-provided ``authorized_tools`` are checked against the
    connector registry. Unknown tools return 422 with the list
    of invalid names — NOT a silent acceptance that fails at
    runtime."""
    src = (REPO / "api" / "v1" / "agents.py").read_text(encoding="utf-8")
    assert '"error": "invalid_authorized_tools"' in src
    assert "do not exist in the connector registry" in src

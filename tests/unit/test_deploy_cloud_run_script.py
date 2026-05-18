from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _deploy_script() -> str:
    return (REPO_ROOT / "scripts" / "deploy_cloud_run.sh").read_text(encoding="utf-8")


def test_cloud_run_deploy_stamps_api_and_ui_commit_metadata() -> None:
    script = _deploy_script()

    assert '"AGENTICORG_GIT_SHA=${DEPLOY_SHA}"' in script
    assert '"GIT_SHA=${DEPLOY_SHA}"' in script
    assert '--update-env-vars="$env_vars"' in script
    assert '"$UI_IMAGE"' in script


def test_ui_metadata_is_set_on_ui_service_update() -> None:
    script = _deploy_script()

    ui_update_calls = [
        line
        for line in script.splitlines()
        if 'update_service_no_traffic UI_NEW_REVISION "$UI_SERVICE"' in line
    ]

    assert len(ui_update_calls) >= 3
    for ui_update_call in ui_update_calls:
        assert '"$UI_IMAGE"' in ui_update_call
        assert '"GIT_SHA=${DEPLOY_SHA}"' in ui_update_call
        assert '"$UI_IMAGE_DIGEST"' in ui_update_call
        assert '"GIT_SHA"' in ui_update_call


def test_pinned_traffic_is_detected_before_rollout() -> None:
    script = _deploy_script()

    assert "PREVIOUS_API_TRAFFIC_SUMMARY" in script
    assert "PREVIOUS_UI_TRAFFIC_SUMMARY" in script
    assert "PREVIOUS_API_TRAFFIC_SPEC" in script
    assert "PREVIOUS_UI_TRAFFIC_SPEC" in script
    assert 'traffic_summary "$API_SERVICE"' in script
    assert 'traffic_to_revisions "$API_SERVICE"' in script


def test_services_are_staged_without_traffic_before_shift() -> None:
    script = _deploy_script()

    assert "--no-traffic" in script
    assert 'update_service_no_traffic API_NEW_REVISION "$API_SERVICE"' in script
    assert 'update_service_no_traffic UI_NEW_REVISION "$UI_SERVICE"' in script
    assert "latest_created_revision" in script
    assert "wait_for_staged_revision_ready" in script


def test_staged_revision_readiness_uses_revision_object_not_latest_ready() -> None:
    script = _deploy_script()
    wait_start = script.index("wait_for_staged_revision_ready()")
    wait_end = script.index("update_service_no_traffic()", wait_start)
    wait_block = script[wait_start:wait_end]

    assert "revision_json" in wait_block
    assert "revision_ready_state" in wait_block
    assert "latest_ready_revision" not in wait_block
    assert "latestReadyRevisionName" not in wait_block
    assert "staged revision object" in wait_block


def test_deploy_script_uses_shell_safe_python_heredocs() -> None:
    script = _deploy_script()

    assert "python3 -c '" not in script
    assert 'python3 -c "' not in script
    assert "python -c '" not in script
    assert 'python -c "' not in script
    assert "<<'PY'" in script
    assert '"$PYTHON_BIN" - "$json_file"' in script


def test_revision_readiness_helper_is_safe_for_unknown_markers() -> None:
    script = _deploy_script()
    ready_start = script.index("revision_ready_state()")
    ready_end = script.index("wait_for_staged_revision_ready()", ready_start)
    ready_block = script[ready_start:ready_end]

    assert '"$PYTHON_BIN" - "$json_file"' in ready_block
    assert "<<'PY'" in ready_block
    assert "python3 -c" not in ready_block
    assert "python -c" not in ready_block
    assert "'<unknown>'" in ready_block
    assert '"<missing>"' in ready_block
    assert '"<none>"' in ready_block


def test_script_resolves_usable_python_for_git_bash_on_windows() -> None:
    script = _deploy_script()

    assert "resolve_python_bin()" in script
    assert 'PYTHON_BIN="$(resolve_python_bin)"' in script
    assert 'for candidate in python3 python; do' in script
    assert "Missing: usable python3 or python" in script


def test_health_poll_json_parsing_uses_shell_safe_helper() -> None:
    script = _deploy_script()
    poll_start = script.index("poll_health_url()")
    poll_end = script.index("print_manual_traffic_commands()", poll_start)
    poll_block = script[poll_start:poll_end]

    assert "json_field_from_stdin" in script
    assert "python3 -c" not in poll_block
    assert "python -c" not in poll_block
    assert 'json_field_from_stdin status' in poll_block
    assert 'json_field_from_stdin commit' in poll_block


def test_retired_ready_staged_revision_is_accepted_when_target_matches() -> None:
    script = _deploy_script()
    ready_start = script.index("revision_ready_state()")
    ready_end = script.index("wait_for_staged_revision_ready()", ready_start)
    ready_block = script[ready_start:ready_end]

    assert 'ready_status == "True"' in ready_block
    assert "Active" not in ready_block
    assert "Retired" not in ready_block
    assert "image and commit metadata matched" in ready_block


def test_staged_revision_ready_false_surfaces_condition_message() -> None:
    script = _deploy_script()
    ready_start = script.index("revision_ready_state()")
    ready_end = script.index("wait_for_staged_revision_ready()", ready_start)
    ready_block = script[ready_start:ready_end]

    assert 'ready_status == "False"' in ready_block
    assert "ready_reason" in ready_block
    assert "ready_message" in ready_block
    assert "is not ready" in ready_block


def test_staged_revision_image_digest_and_commit_metadata_are_verified() -> None:
    script = _deploy_script()
    ready_start = script.index("revision_ready_state()")
    ready_end = script.index("wait_for_staged_revision_ready()", ready_start)
    ready_block = script[ready_start:ready_end]

    assert "image mismatch" in ready_block
    assert "commit metadata mismatch" in ready_block
    assert "image_digest" in ready_block
    assert "expected_sha" in ready_block
    assert "serving.knative.dev/service" in ready_block


def test_dry_run_reports_planned_traffic_changes() -> None:
    script = _deploy_script()

    assert "[dry-run] would update $API_SERVICE image/env with --no-traffic" in script
    assert "[dry-run] would route API traffic 100% to $API_NEW_REVISION" in script
    assert (
        "[dry-run] would stage and route UI traffic 100% to $UI_NEW_REVISION only after API health passes"
        in script
    )
    assert (
        "[dry-run] would check staged API/UI readiness from revision objects, not service latestReadyRevisionName"
        in script
    )


def test_traffic_modes_are_explicit_and_preserve_reports_not_deployed() -> None:
    script = _deploy_script()

    assert "--traffic <mode>" in script
    assert "latest|preserve|manual" in script
    assert "NOT DEPLOYED: --traffic preserve staged revisions" in script
    assert "NOT DEPLOYED: --traffic manual staged revisions" in script
    assert "print_manual_traffic_commands" in script


def test_normal_mode_routes_traffic_to_captured_target_revisions() -> None:
    script = _deploy_script()

    assert 'move_traffic_to_revision "$API_SERVICE" "$API_NEW_REVISION"' in script
    assert 'move_traffic_to_revision "$UI_SERVICE" "$UI_NEW_REVISION"' in script
    assert '--to-revisions="$revision=100"' in script
    assert "DEPLOYED: API=$API_NEW_REVISION UI=$UI_NEW_REVISION" in script


def test_api_failure_rolls_back_ui_traffic_plan() -> None:
    script = _deploy_script()
    failure_start = script.index('if ! poll_health_url "$HEALTH_URL"')
    failure_end = script.index('if ! move_traffic_to_revision "$UI_SERVICE"', failure_start)
    failure_block = script[failure_start:failure_end]

    assert 'rollback_service_traffic "$API_SERVICE" "$PREVIOUS_API_TRAFFIC_SPEC"' in failure_block
    assert 'rollback_service_traffic "$UI_SERVICE" "$PREVIOUS_UI_TRAFFIC_SPEC"' in failure_block


def test_ui_is_staged_only_after_api_public_health_in_latest_mode() -> None:
    script = _deploy_script()

    api_stage = script.index(
        'update_service_no_traffic API_NEW_REVISION "$API_SERVICE"'
    )
    public_health = script.index('poll_health_url "$HEALTH_URL" "public API" 30')
    ui_stage = script.index('update_service_no_traffic UI_NEW_REVISION "$UI_SERVICE"', public_health)
    ui_traffic = script.index('move_traffic_to_revision "$UI_SERVICE" "$UI_NEW_REVISION"')

    assert api_stage < public_health < ui_stage < ui_traffic


def test_script_refuses_success_when_public_health_reports_old_sha() -> None:
    script = _deploy_script()

    assert 'poll_health_url "$HEALTH_URL" "public API" 30' in script
    assert "health did not converge to $SHORT_SHA" in script
    assert 'commit=${commit:-?} (want $SHORT_SHA)' in script
    assert script.index('poll_health_url "$HEALTH_URL" "public API" 30') < script.index(
        "DEPLOYED: API=$API_NEW_REVISION"
    )

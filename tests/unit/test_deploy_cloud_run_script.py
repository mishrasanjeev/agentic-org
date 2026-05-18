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
    ui_update_start = script.index(
        'update_service_no_traffic UI_NEW_REVISION "$UI_SERVICE"'
    )
    ui_update_end = script.index('echo "Staged API revision:', ui_update_start)
    ui_update_block = script[ui_update_start:ui_update_end]

    assert '"$UI_IMAGE"' in ui_update_block
    assert '"GIT_SHA=${DEPLOY_SHA}"' in ui_update_block


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
    assert "wait_for_revision_ready" in script


def test_dry_run_reports_planned_traffic_changes() -> None:
    script = _deploy_script()

    assert "[dry-run] would update $API_SERVICE image/env with --no-traffic" in script
    assert "[dry-run] would route API traffic 100% to $API_NEW_REVISION" in script
    assert (
        "[dry-run] would route UI traffic 100% to $UI_NEW_REVISION only after API health passes"
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


def test_script_refuses_success_when_public_health_reports_old_sha() -> None:
    script = _deploy_script()

    assert 'poll_health_url "$HEALTH_URL" "public API" 30' in script
    assert "health did not converge to $SHORT_SHA" in script
    assert 'commit=${commit:-?} (want $SHORT_SHA)' in script
    assert script.index('poll_health_url "$HEALTH_URL" "public API" 30') < script.index(
        "DEPLOYED: API=$API_NEW_REVISION"
    )

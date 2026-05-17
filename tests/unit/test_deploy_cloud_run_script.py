from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_cloud_run_deploy_stamps_api_and_ui_commit_metadata() -> None:
    script = (REPO_ROOT / "scripts" / "deploy_cloud_run.sh").read_text(encoding="utf-8")

    assert '--update-env-vars="AGENTICORG_GIT_SHA=${DEPLOY_SHA}"' in script
    assert '--update-env-vars="GIT_SHA=${DEPLOY_SHA}"' in script
    assert "--image=\"$UI_IMAGE\"" in script


def test_ui_metadata_is_set_on_ui_service_update() -> None:
    script = (REPO_ROOT / "scripts" / "deploy_cloud_run.sh").read_text(encoding="utf-8")
    ui_update_start = script.index('run gcloud run services update "$UI_SERVICE"')
    ui_update_end = script.index("if [[ $DRY_RUN -eq 1 ]]; then", ui_update_start)
    ui_update_block = script[ui_update_start:ui_update_end]

    assert "--image=\"$UI_IMAGE\"" in ui_update_block
    assert '--update-env-vars="GIT_SHA=${DEPLOY_SHA}"' in ui_update_block

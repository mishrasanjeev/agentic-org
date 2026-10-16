"""Execute the actual optional deploy block without contacting a cloud service."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("value", [None, "true", "false", "yes"])
def test_explanation_option_is_explicit_api_only(value):
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("Bash required; exercised in the Linux container gate")
    source = (ROOT / "scripts/deploy_cloud_run.sh").read_text(encoding="utf-8")
    block = source.split("# Direct-run explanations read existing audit evidence;", 1)[1].split(
        "# Optional model rollout:",
        1,
    )[0]
    env = dict(os.environ)
    env.pop("DEPLOY_AGENT_EXPLANATION_AUDIT", None)
    if value is not None:
        env["DEPLOY_AGENT_EXPLANATION_AUDIT"] = value
    result = subprocess.run(  # noqa: S603 - executes this repository's block with fixed test inputs
        [
            bash,
            "-c",
            "API_UPDATE_ENV_VARS=base\nBACKGROUND_UPDATE_ENV_VARS=worker\n"
            + block
            + '\nprintf "%s\\n%s\\n" "$API_UPDATE_ENV_VARS" "$BACKGROUND_UPDATE_ENV_VARS"',
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if value == "yes":
        assert result.returncode != 0
        assert "must be true or false" in result.stderr
    else:
        assert result.returncode == 0
        expected = "base" if value is None else f"base,AGENTICORG_AGENT_EXPLANATION_AUDIT_ENABLED={value}"
        assert result.stdout.splitlines() == [expected, "worker"]

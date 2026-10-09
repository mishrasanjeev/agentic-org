# SPDX-License-Identifier: Apache-2.0
"""The cross-repository decision-grant join runs in CI, and so do the governed-case suites.

``ui/e2e/decision-grants.spec.ts`` (``make e2e-decisions``) proves the join between the console,
the API and the real Grantex auth service, but no workflow ran it, and the governed-case suites
(``ui/e2e/governed-cases*.spec.ts``) skipped in the one that runs ``make e2e`` because it never set
``AGENTICORG_SEED_PASSWORD`` nor ran ``make seed-cases``. The ``e2e-decisions`` guard did not check
the auth service's administrator key (the runner fell back to a placeholder the service was never
given) or the case decision service, so a run without them failed late and obscurely.

These tests pin the workflow, the Makefile guards and the compose wiring that fix that.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github" / "workflows" / "local-stack.yml"
MAKEFILE = REPO / "Makefile"
COMPOSE = REPO / "docker-compose.dev.yml"

DECISIONS_JOB = "decision-grants"
DECISIONS_JOB_NAME = "make e2e-decisions"
# Values the decision-grant run needs that are secrets: generated per run, never written down.
PER_RUN_SECRETS = ("AGENTICORG_SEED_PASSWORD", "AGENTICORG_DEV_GRANTEX_ADMIN_KEY")


def _workflow() -> dict[str, Any]:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list(job.get("steps") or [])


def _run_lines(job: dict[str, Any]) -> list[str]:
    """Every shell line of the job's ``run`` steps, in order."""
    lines: list[str] = []
    for step in _steps(job):
        lines.extend(line.strip() for line in str(step.get("run") or "").splitlines() if line.strip())
    return lines


def _index(lines: list[str], command: str) -> int:
    matches = [i for i, line in enumerate(lines) if line == command or line.startswith(f"{command} ")]
    assert matches, f"the job never runs `{command}`"
    return matches[0]


def _generates_per_run_secrets(job: dict[str, Any], names: tuple[str, ...]) -> None:
    """Each secret is generated at run time, masked, exported through $GITHUB_ENV and never literal."""
    literal_env = {**(_workflow().get("env") or {}), **(job.get("env") or {})}
    for step in _steps(job):
        literal_env.update(step.get("env") or {})
    script = "\n".join(str(step.get("run") or "") for step in _steps(job))
    assert '>> "$GITHUB_ENV"' in script, "the generated values are not exported to later steps"
    for name in names:
        assert name not in literal_env, f"{name} must be generated per run, not written into the workflow"
        exported = re.search(rf'^\s*echo "{name}=\$([a-z_]+)"(?: >> "\$GITHUB_ENV")?$', script, re.M)
        assert exported, f"{name} is not exported to later steps"
        variable = exported.group(1)
        assert re.search(rf'^\s*{variable}="\$\(openssl rand -hex \d+\)"$', script, re.M), (
            f"{name} is not generated with openssl rand"
        )
        assert f'echo "::add-mask::${variable}"' in script, f"{name} is not masked in the log"


def test_the_workflow_runs_on_every_pull_request_and_push_to_main() -> None:
    workflow = _workflow()
    # PyYAML reads the bare key `on` as the boolean True.
    triggers = workflow.get("on", workflow.get(True))
    assert "pull_request" in triggers
    assert triggers["push"]["branches"] == ["main"]


def test_a_decision_grant_job_starts_the_stack_with_decisions_on_and_runs_the_suite() -> None:
    job = _workflow()["jobs"].get(DECISIONS_JOB)
    assert job is not None, f"no `{DECISIONS_JOB}` job in {WORKFLOW.name}"
    # The check name the owner makes required.
    assert job["name"] == DECISIONS_JOB_NAME
    env = job.get("env") or {}
    assert str(env.get("AGENTICORG_DEV_DECISION_GRANTS")).lower() == "true"
    assert env.get("AGENTICORG_DEV_CASE_DECISION_SERVICE") == "grantex"
    _generates_per_run_secrets(job, PER_RUN_SECRETS)

    lines = _run_lines(job)
    order = [_index(lines, command) for command in ("make dev", "make seed", "make seed-cases", "make e2e-decisions")]
    assert order == sorted(order), "make dev, seed, seed-cases and e2e-decisions run in that order"


def test_the_decision_grant_job_uploads_its_report_on_failure_and_always_cleans_up() -> None:
    job = _workflow()["jobs"][DECISIONS_JOB]
    uploads = [s for s in _steps(job) if str(s.get("uses", "")).startswith("actions/upload-artifact@")]
    assert uploads, "the Playwright report is not uploaded"
    [upload] = uploads
    assert upload.get("if") == "failure()"
    paths = str(upload["with"]["path"])
    assert "ui/playwright-report/decision-grants" in paths
    assert "ui/test-results/decision-grants" in paths
    cleanup = [s for s in _steps(job) if str(s.get("run", "")).strip() == "make clean"]
    assert cleanup and cleanup[-1].get("if") == "always()"


def test_the_dev_stack_job_seeds_governed_cases_so_their_suites_run_instead_of_skipping() -> None:
    """``governed-cases*.spec.ts`` skip without the seed password or the seeded cases.

    They run in this job, on a stack with no decision-grant issuer, because
    ``governed-cases-decision.spec.ts`` asserts exactly that refusal
    (``decision_service_not_configured``).
    """
    job = _workflow()["jobs"]["dev-and-test"]
    _generates_per_run_secrets(job, ("AGENTICORG_SEED_PASSWORD",))
    assert "AGENTICORG_DEV_CASE_DECISION_SERVICE" not in (job.get("env") or {})
    lines = _run_lines(job)
    order = [_index(lines, command) for command in ("make seed", "make seed-cases", "make e2e")]
    assert order == sorted(order)


def _e2e_decisions_recipe() -> str:
    text = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(r"^e2e-decisions:\n((?:\t.*\n)+)", text, re.M)
    assert match, "no e2e-decisions recipe"
    return match.group(1)


def test_the_e2e_decisions_guards_come_before_anything_touches_the_stack() -> None:
    recipe = _e2e_decisions_recipe()
    first_compose = recipe.index("$(COMPOSE)")
    for variable in ("AGENTICORG_DEV_GRANTEX_ADMIN_KEY", "AGENTICORG_DEV_CASE_DECISION_SERVICE"):
        assert variable in recipe[:first_compose], f"e2e-decisions does not check {variable} first"


_MAKE = shutil.which("make")
_BASH = shutil.which("bash")


def _make_e2e_decisions(**overrides: str) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGENTICORG_")}
    env.update(
        AGENTICORG_DEV_DECISION_GRANTS="true",
        AGENTICORG_SEED_PASSWORD="local-only-passphrase-for-tests",
        AGENTICORG_DEV_GRANTEX_ADMIN_KEY="local-only-admin-key-for-tests",
        AGENTICORG_DEV_CASE_DECISION_SERVICE="grantex",
    )
    for name, value in overrides.items():
        if value:
            env[name] = value
        else:
            env.pop(name, None)
    # A command that fails the test if a guard lets the run reach the stack.
    reached = "echo reached-the-stack; exit 97"
    return subprocess.run(  # noqa: S603 - fixed arguments, no shell
        [_MAKE or "make", "--no-print-directory", "-C", str(REPO), "e2e-decisions", f"COMPOSE={reached}"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


@pytest.mark.skipif(_MAKE is None or _BASH is None, reason="needs make and bash (the tools image has both)")
@pytest.mark.parametrize(
    ("variable", "value", "message"),
    [
        ("AGENTICORG_DEV_GRANTEX_ADMIN_KEY", "", "AGENTICORG_DEV_GRANTEX_ADMIN_KEY"),
        ("AGENTICORG_DEV_CASE_DECISION_SERVICE", "", "AGENTICORG_DEV_CASE_DECISION_SERVICE=grantex"),
        ("AGENTICORG_DEV_CASE_DECISION_SERVICE", "none", "AGENTICORG_DEV_CASE_DECISION_SERVICE=grantex"),
    ],
)
def test_make_e2e_decisions_refuses_to_start_without_the_admin_key_or_the_decision_service(
    variable: str, value: str, message: str
) -> None:
    result = _make_e2e_decisions(**{variable: value})
    assert result.returncode != 0
    assert message in result.stderr
    assert "reached-the-stack" not in result.stdout + result.stderr


def _grantex_environment() -> dict[str, Any]:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return compose["services"]["grantex"]["environment"]


def test_the_suite_runner_uses_exactly_the_admin_key_the_auth_service_was_given() -> None:
    """No placeholder fallback: a missing key fails as missing, not as a 401 from a mismatch."""
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    runner = compose["services"]["e2e-decisions"]["environment"]
    assert runner["GRANTEX_ADMIN_API_KEY"] == _grantex_environment()["ADMIN_API_KEY"]


def test_seed_cases_talks_to_the_stacks_own_grantex_with_its_sandbox_developer_key() -> None:
    """The sample cases need a development root grant, which only a sandbox developer can obtain
    without a principal's passkey; the seed must never fall back to a hosted issuer."""
    makefile = MAKEFILE.read_text(encoding="utf-8")
    match = re.search(r"^seed-cases:.*\n((?:\t.*\n)+)", makefile, re.M)
    assert match, "no seed-cases recipe"
    recipe = match.group(1)
    assert "GRANTEX_BASE_URL=http://grantex:" in recipe
    assert "GRANTEX_API_KEY=$(DEV_GRANTEX_SANDBOX_KEY)" in recipe
    key = re.search(r"^DEV_GRANTEX_SANDBOX_KEY \?= (\S+)$", makefile, re.M)
    assert key, "DEV_GRANTEX_SANDBOX_KEY is not defined"
    assert _grantex_environment().get("SEED_SANDBOX_KEY") == key.group(1)


# ── A skip is not a pass ─────────────────────────────────────────────────────

HELPER = REPO / "ui" / "e2e" / "helpers" / "governed-cases.ts"
REQUIRE_FLAG = "AGENTICORG_E2E_REQUIRE_GOVERNED_CASES"


def _e2e_recipe() -> str:
    match = re.search(r"^e2e:\n((?:\t.*\n)+)", MAKEFILE.read_text(encoding="utf-8"), re.M)
    assert match, "no e2e recipe"
    return match.group(1)


def test_ci_fails_rather_than_skips_when_the_governed_case_prerequisites_do_not_arrive() -> None:
    """If the seed file or the seed password stops reaching the runner, the governed-case suites
    would skip and the job would stay green; in CI a missing prerequisite must fail the run."""
    job = _workflow()["jobs"]["dev-and-test"]
    assert str((job.get("env") or {}).get(REQUIRE_FLAG)).lower() == "true", (
        f"the dev-and-test job does not set {REQUIRE_FLAG}"
    )
    assert f"-e {REQUIRE_FLAG}" in _e2e_recipe(), f"make e2e does not pass {REQUIRE_FLAG} to the runner"
    helper = HELPER.read_text(encoding="utf-8")
    body = re.search(r"export function missingPrerequisite\(\): string \{\n(.*?)\n\}", helper, re.S)
    assert body, "missingPrerequisite() not found"
    assert f'process.env.{REQUIRE_FLAG} === "true"' in body.group(1)
    assert "throw new Error(" in body.group(1), "a required but missing prerequisite must throw, not skip"


def test_catalogue_and_connector_replays_use_the_password_generated_by_ci() -> None:
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    runner = compose["services"]["e2e"]["environment"]
    assert runner["AGENTICORG_SEED_PASSWORD"] == "${AGENTICORG_SEED_PASSWORD:-}"
    assert runner["AGENTICORG_E2E_NATIVE_CONNECTOR_PREFILL_ENABLED"] == (
        "${AGENTICORG_DEV_NATIVE_CONNECTOR_PREFILL_ENABLED:-false}"
    )
    job = _workflow()["jobs"]["dev-and-test"]
    assert str(job["env"]["AGENTICORG_DEV_NATIVE_CONNECTOR_PREFILL_ENABLED"]).lower() == "true"
    assert "-e AGENTICORG_SEED_PASSWORD" in _e2e_recipe()
    browser = (REPO / "ui" / "e2e" / "dev-stack.spec.ts").read_text(encoding="utf-8")
    assert "AGENTICORG_DEV_SEED_PASSWORD" not in browser
    assert browser.count("const password = process.env.AGENTICORG_SEED_PASSWORD;") == 2
    assert browser.count('expect(password, "Local seed password is required").toBeTruthy();') == 2
    native = browser.split('test("native connector registration respects rollout and persists registry identity"', 1)[1]
    assert "test.skip(" not in native, "missing seed credentials must fail instead of skipping registration"
    assert 'process.env.AGENTICORG_E2E_NATIVE_CONNECTOR_PREFILL_ENABLED !== "true"' in native
    assert 'toHaveValue("custom")' in native


def test_the_dev_postgres_healthcheck_waits_for_the_tcp_listener() -> None:
    """On a fresh volume the image's init-time server listens on the Unix socket only, so a socket
    ``pg_isready`` reports healthy before grantex-db and migrate can connect over TCP."""
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    test = " ".join(compose["services"]["postgres"]["healthcheck"]["test"])
    assert "pg_isready -h 127.0.0.1 " in test


def test_the_tools_image_has_make_so_the_guard_tests_run_in_ci() -> None:
    """``make test`` runs in the tools image; without make the behavioural guard tests above skip."""
    dockerfile = (REPO / "Dockerfile.tools").read_text(encoding="utf-8")
    install = re.search(r"apt-get install -y --no-install-recommends \\\s*\n(.*?)&&", dockerfile, re.S)
    assert install, "no apt-get install in Dockerfile.tools"
    assert re.search(r"(?<![\w-])make(?![\w-])", install.group(1)), "Dockerfile.tools does not install make"

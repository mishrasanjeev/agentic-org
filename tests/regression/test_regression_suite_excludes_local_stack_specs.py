# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The production Playwright regression suite never runs local-stack specs.

``ui/e2e/regression.config.ts`` runs every ``*.spec.ts`` against production. The specs
owned by the local-stack configs (``dev-stack``, ``decision-grants``, ``onboarding-desk``)
need the dev stack's seeded data and dev-only secrets; ``decision-grants.spec.ts`` and
``onboarding-desk.spec.ts`` also throw at load without their variables, which aborted the
post-deploy run. Every spec those configs select must be in the regression config's
``testIgnore``, and every spec that loads the decision-grant helpers must belong to one.
"""

from __future__ import annotations

import re
from pathlib import Path

E2E = Path(__file__).resolve().parents[2] / "ui" / "e2e"
LOCAL_STACK_CONFIGS = ("dev-stack.config.ts", "decision-grants.config.ts", "onboarding-desk.config.ts")


def _patterns(config: str, key: str) -> list[str]:
    text = (E2E / config).read_text(encoding="utf-8")
    match = re.search(rf"{key}:\s*\[([^\]]*)\]", text)
    assert match, f"{config} has no {key} list"
    return re.findall(r'"([^"]+)"', match.group(1))


def _local_stack_specs() -> set[str]:
    return {pattern for config in LOCAL_STACK_CONFIGS for pattern in _patterns(config, "testMatch")}


def test_every_local_stack_spec_is_ignored_by_the_regression_suite() -> None:
    ignored = set(_patterns("regression.config.ts", "testIgnore"))
    owned = _local_stack_specs()
    assert owned <= ignored, f"regression.config.ts runs local-stack specs: {sorted(owned - ignored)}"


def test_the_ignored_patterns_still_name_real_specs() -> None:
    for pattern in _patterns("regression.config.ts", "testIgnore"):
        assert list(E2E.glob(pattern)), f"testIgnore pattern {pattern!r} matches no spec"


def test_every_spec_that_loads_the_decision_grant_helpers_is_a_local_stack_spec() -> None:
    # Those helpers throw at load without the local stack's variables.
    loads_helpers = {
        spec.name for spec in E2E.glob("*.spec.ts") if "helpers/decision-grants" in spec.read_text(encoding="utf-8")
    }
    missing = loads_helpers - _local_stack_specs()
    assert not missing, f"specs that need the local stack but no local-stack config: {sorted(missing)}"

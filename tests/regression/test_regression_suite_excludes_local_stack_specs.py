# SPDX-License-Identifier: Apache-2.0
"""The production Playwright regression suite never runs local-stack specs.

``ui/e2e/regression.config.ts`` runs every ``*.spec.ts`` against production. The specs
owned by ``dev-stack.config.ts`` and ``decision-grants.config.ts`` need the dev stack's
seeded data and dev-only secrets; ``decision-grants.spec.ts`` also throws at load without
its variables, which aborted the post-deploy run. Every spec another config selects must
be in the regression config's ``testIgnore``.
"""

from __future__ import annotations

import re
from pathlib import Path

E2E = Path(__file__).resolve().parents[2] / "ui" / "e2e"


def _patterns(config: str, key: str) -> list[str]:
    text = (E2E / config).read_text(encoding="utf-8")
    match = re.search(rf"{key}:\s*\[([^\]]*)\]", text)
    assert match, f"{config} has no {key} list"
    return re.findall(r'"([^"]+)"', match.group(1))


def test_every_local_stack_spec_is_ignored_by_the_regression_suite() -> None:
    ignored = set(_patterns("regression.config.ts", "testIgnore"))
    owned = set(_patterns("dev-stack.config.ts", "testMatch")) | set(
        _patterns("decision-grants.config.ts", "testMatch")
    )
    assert owned <= ignored, f"regression.config.ts runs local-stack specs: {sorted(owned - ignored)}"


def test_the_ignored_patterns_still_name_real_specs() -> None:
    for pattern in _patterns("regression.config.ts", "testIgnore"):
        assert list(E2E.glob(pattern)), f"testIgnore pattern {pattern!r} matches no spec"

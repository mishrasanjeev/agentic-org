# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Write the build snapshot of the public plan catalog: ``ui/src/content/publicPlanCatalog.generated.json``.

The file is ``PUBLIC_PLAN_CATALOG`` from ``core/billing/catalog.py`` in the
same JSON shape that ``GET /api/v1/billing/plans`` returns. The pricing page
renders it before the API answers, so the prerendered HTML carries the plans.
``--check`` exits 1 when the file is out of date instead of writing it.

    python scripts/generate_public_plan_catalog.py [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "ui" / "src" / "content" / "publicPlanCatalog.generated.json"


def render() -> str:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from core.billing.catalog import PUBLIC_PLAN_CATALOG

    return json.dumps(PUBLIC_PLAN_CATALOG.model_dump(mode="json"), indent=2) + "\n"


def _current() -> str:
    return OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when the file is out of date")
    args = parser.parse_args(argv)
    expected = render()
    if args.check:
        if _current() != expected:
            print(f"{OUTPUT.relative_to(REPO_ROOT)} is out of date; run scripts/generate_public_plan_catalog.py")
            return 1
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

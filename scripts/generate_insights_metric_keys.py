# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Write the Insights metric keys the UI may reference: ``ui/src/lib/insights/metricKeys.generated.json``.

The file is the sorted list of keys in ``core/insights/registry.py``.
``--check`` exits 1 when the file is out of date instead of writing it.

    python scripts/generate_insights_metric_keys.py [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "ui" / "src" / "lib" / "insights" / "metricKeys.generated.json"


def render() -> str:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from core.insights.registry import metric_keys

    return json.dumps(metric_keys(), indent=2) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when the file is out of date")
    args = parser.parse_args(argv)
    expected = render()
    if args.check:
        current = OUTPUT.read_text(encoding="utf-8") if OUTPUT.exists() else ""
        if current != expected:
            print(f"{OUTPUT.relative_to(REPO_ROOT)} is out of date; run scripts/generate_insights_metric_keys.py")
            return 1
        return 0
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

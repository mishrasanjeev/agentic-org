# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Write the Insights keys the UI may reference: ``ui/src/lib/insights/metricKeys.generated.json``.

That file is the sorted list of metric keys in ``core/insights/registry.py``;
``explorerKeys.generated.json`` beside it is the sorted list of explorer keys
in ``core/insights/explore_registry.py``. ``--check`` exits 1 when either file
is out of date instead of writing them.

    python scripts/generate_insights_metric_keys.py [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = REPO_ROOT / "ui" / "src" / "lib" / "insights" / "metricKeys.generated.json"
EXPLORER_FILE = "explorerKeys.generated.json"


def _import_path() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


def render() -> str:
    _import_path()
    from core.insights.registry import metric_keys

    return json.dumps(metric_keys(), indent=2) + "\n"


def render_explorers() -> str:
    _import_path()
    from core.insights.explore_registry import EXPLORERS

    return json.dumps(sorted(EXPLORERS), indent=2) + "\n"


def _current(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="fail when a file is out of date")
    args = parser.parse_args(argv)
    files = {OUTPUT: render(), OUTPUT.parent / EXPLORER_FILE: render_explorers()}
    if args.check:
        stale = [path for path, expected in files.items() if _current(path) != expected]
        for path in stale:
            print(f"{path.relative_to(REPO_ROOT)} is out of date; run scripts/generate_insights_metric_keys.py")
        return 1 if stale else 0
    for path, expected in files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(expected, encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

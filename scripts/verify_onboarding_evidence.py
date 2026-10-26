# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Check an onboarding desk evidence pack offline.

    python scripts/verify_onboarding_evidence.py pack.json --public-key <base64>

The public key is the ``public_key`` from ``GET /api/v1/onboarding-desk/evidence-key``.
Without ``--public-key`` only the section digests are checked. Exits 0 when the pack
verifies and 1 with each problem listed when it does not.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    from core.onboarding_desk.evidence import verify

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pack", type=Path)
    parser.add_argument("--public-key", default=None)
    args = parser.parse_args(argv)
    try:
        pack = json.loads(args.pack.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read the pack: {exc}")
        return 1
    problems = verify(pack, args.public_key)
    if problems:
        for problem in problems:
            print(f"FAIL {problem}")
        return 1
    what = "digests and signature" if args.public_key else "digests (signature not checked: no --public-key)"
    print(f"OK {what} verify for application {pack['manifest'].get('application_id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

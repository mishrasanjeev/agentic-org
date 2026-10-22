#!/usr/bin/env python3
# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Release the Firebase Hosting site that fronts merchant custom domains.

The site serves no files. Its only configuration is the rewrites in
``core.commerce.custom_domains.firebase_site_config``: the agent paths
(``/.well-known/ucp``, ``/.well-known/ucp/**``, ``/ucp/**``, ``/acp/**`` and
``/.well-known/agenticorg-host``) go to the API's Cloud Run service; every
other path is Firebase's own 404. Run it once per site, and again whenever the
agent paths change (it creates, finalizes and releases a new version through
the Firebase Hosting REST API with the caller's Application Default
Credentials):

    python scripts/release_merchant_domain_site.py --project PROJECT_ID --site SITE_ID
    python scripts/release_merchant_domain_site.py --project PROJECT_ID --site SITE_ID --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.commerce.custom_domains import FIREBASE_API, firebase_site_config  # noqa: E402

SCOPE = "https://www.googleapis.com/auth/cloud-platform"


def _session(project: str) -> Any:
    import google.auth  # noqa: PLC0415
    from google.auth.transport.requests import AuthorizedSession  # noqa: PLC0415

    credentials, _ = google.auth.default(scopes=[SCOPE])
    session = AuthorizedSession(credentials)
    session.headers["x-goog-user-project"] = project
    return session


def _check(response: Any, step: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise SystemExit(f"{step} failed: HTTP {response.status_code} {response.text[:500]}")
    return response.json()


def release(*, project: str, site: str, service: str, region: str, dry_run: bool) -> int:
    config = firebase_site_config(service=service, region=region)
    print(json.dumps({"site": site, "config": config}, indent=2))
    if dry_run:
        return 0
    session = _session(project)
    base = f"{FIREBASE_API}/sites/{site}"
    version = _check(session.post(f"{base}/versions", json={"config": config}, timeout=30), "create version")
    name = version["name"]
    _check(
        session.patch(f"{FIREBASE_API}/{name}?update_mask=status", json={"status": "FINALIZED"}, timeout=30),
        "finalize version",
    )
    released = _check(session.post(f"{base}/releases?versionName={name}", json={}, timeout=30), "release")
    print(f"released {released.get('name')} ({name}) on https://{site}.web.app")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--project", required=True)
    parser.add_argument(
        "--site", required=True, help="Firebase Hosting site id (AGENTICORG_CUSTOM_DOMAIN_FIREBASE_SITE)"
    )
    parser.add_argument("--service", default="agenticorg-api")
    parser.add_argument("--region", default="asia-southeast1")
    parser.add_argument("--dry-run", action="store_true", help="print the config only")
    args = parser.parse_args(argv)
    return release(project=args.project, site=args.site, service=args.service, region=args.region, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())

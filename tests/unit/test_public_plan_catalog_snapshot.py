# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The pricing page's build snapshot must match the catalog the API serves."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.encoders import jsonable_encoder

import scripts.generate_public_plan_catalog as gen
from core.billing.catalog import PUBLIC_PLAN_CATALOG

REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO_ROOT / "ui" / "src" / "content" / "publicPlanCatalog.generated.json"


def test_snapshot_matches_the_catalog_the_api_returns() -> None:
    snapshot = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert snapshot == jsonable_encoder(PUBLIC_PLAN_CATALOG)
    assert SNAPSHOT.read_text(encoding="utf-8").replace("\r\n", "\n") == gen.render()


def test_generator_writes_the_snapshot_and_checks_it(tmp_path, monkeypatch, capsys) -> None:
    out = tmp_path / "ui" / "src" / "content" / "publicPlanCatalog.generated.json"
    monkeypatch.setattr(gen, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(gen, "OUTPUT", out)

    # Missing file: --check fails and names the file; a plain run writes it.
    assert gen.main(["--check"]) == 1
    assert "publicPlanCatalog.generated.json is out of date" in capsys.readouterr().out
    assert gen.main([]) == 0
    assert json.loads(out.read_text(encoding="utf-8")) == jsonable_encoder(PUBLIC_PLAN_CATALOG)
    assert gen.main(["--check"]) == 0

    # A stale price fails the check until the file is rewritten.
    stale = json.loads(out.read_text(encoding="utf-8"))
    stale["plans"][1]["prices"][0]["amount_minor"] += 100
    out.write_text(json.dumps(stale, indent=2) + "\n", encoding="utf-8")
    assert gen.main(["--check"]) == 1
    assert gen.main([]) == 0
    assert gen.main(["--check"]) == 0

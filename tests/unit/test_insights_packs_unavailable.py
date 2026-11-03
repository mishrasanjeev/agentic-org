# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Until the packs land, asking for a view PDF, finance pack or evidence pack is refused."""

from __future__ import annotations

import pytest

from core.insights import packs
from core.insights.export_jobs import ExportJobError


@pytest.mark.parametrize("kind", ["view_pdf", "finance_pack", "evidence_pack"])
def test_pack_kinds_are_refused_until_they_are_built(kind: str) -> None:
    with pytest.raises(ExportJobError) as refused:
        packs.validate(kind, {}, None)
    assert refused.value.error == "kind_unavailable"

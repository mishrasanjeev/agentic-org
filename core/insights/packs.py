# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""View PDFs, the finance pack and the period evidence pack (built by the export worker).

Not available yet: asking for one of these kinds is refused.
"""

from __future__ import annotations

from typing import Any


def validate(kind: str, params: dict[str, Any], caller: Any) -> dict[str, Any]:
    from core.insights.export_jobs import ExportJobError

    raise ExportJobError("kind_unavailable", f"{kind} exports are not available yet")

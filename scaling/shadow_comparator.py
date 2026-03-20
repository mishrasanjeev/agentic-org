"""Shadow mode comparator."""
from __future__ import annotations
from typing import Any

class ShadowComparator:
    async def compare(self, shadow_output: dict, reference_output: dict) -> dict[str, Any]:
        match = shadow_output == reference_output
        score = 1.0 if match else self._compute_similarity(shadow_output, reference_output)
        return {"outputs_match": match, "match_score": score}

    def _compute_similarity(self, a: dict, b: dict) -> float:
        if not a or not b:
            return 0.0
        common_keys = set(a.keys()) & set(b.keys())
        if not common_keys:
            return 0.0
        matches = sum(1 for k in common_keys if a[k] == b[k])
        return matches / max(len(a), len(b))

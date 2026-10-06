# SPDX-License-Identifier: Apache-2.0
"""Deterministic metrics over the per-case outcomes of an evaluation run.

Everything here is arithmetic over outcomes the scorer already produced: no
model is called and no answer text is read. A run's ``results`` carry, per
case, whether it passed, which expectations failed, and for a labelled case the
expected and the predicted label; the metrics summarise them:

* ``pass_rate``: passed over scored cases (errors are not scored);
* ``exact_match``: over the cases that carry ``equals``, how many matched;
* ``classification``: over the cases that carry ``label``, accuracy and the
  macro-averaged precision, recall and F1 across the dataset's labels, with
  the per-label counts. An answer that names no label counts as a miss for
  its expected label and no false positive for any other.
"""

from __future__ import annotations

from typing import Any


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 4) if whole else None


def exact_match(results: list[dict[str, Any]]) -> dict[str, Any]:
    scored = [entry for entry in results if entry.get("result") != "error" and entry.get("has_equals")]
    matched = sum(1 for entry in scored if "equals" not in (entry.get("failed_checks") or []))
    return {"cases": len(scored), "matched": matched, "rate": _rate(matched, len(scored))}


def classification(results: list[dict[str, Any]], labels: tuple[str, ...]) -> dict[str, Any] | None:
    scored = [entry for entry in results if entry.get("result") != "error" and entry.get("label")]
    if not scored or not labels:
        return None
    per_label: dict[str, dict[str, int]] = {label: {"expected": 0, "predicted": 0, "correct": 0} for label in labels}
    correct = 0
    for entry in scored:
        expected = entry["label"]["expected"]
        predicted = entry["label"].get("predicted")
        per_label[expected]["expected"] += 1
        if predicted in per_label:
            per_label[predicted]["predicted"] += 1
        if predicted == expected:
            per_label[expected]["correct"] += 1
            correct += 1
    precisions, recalls, f1s = [], [], []
    detail: dict[str, Any] = {}
    for label, counts in per_label.items():
        precision = counts["correct"] / counts["predicted"] if counts["predicted"] else 0.0
        recall = counts["correct"] / counts["expected"] if counts["expected"] else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        precisions.append(precision)
        recalls.append(recall)
        f1s.append(f1)
        detail[label] = {**counts, "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}
    count = len(labels)
    return {
        "cases": len(scored),
        "accuracy": _rate(correct, len(scored)),
        "precision": round(sum(precisions) / count, 4),
        "recall": round(sum(recalls) / count, 4),
        "f1": round(sum(f1s) / count, 4),
        "labels": detail,
    }


def summarise(results: list[dict[str, Any]], labels: tuple[str, ...]) -> dict[str, Any]:
    scored = [entry for entry in results if entry.get("result") != "error"]
    passed = sum(1 for entry in scored if entry.get("result") == "passed")
    return {
        "pass_rate": _rate(passed, len(scored)),
        "exact_match": exact_match(results),
        "classification": classification(results, labels),
    }

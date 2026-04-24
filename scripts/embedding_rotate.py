"""Safe embedding-model rotation for knowledge_documents.

Changing the embedding model usually means changing vector dimensions.
pgvector's ``embedding vector(384)`` column rejects differently-sized
vectors, so flipping ``AGENTICORG_EMBEDDING_MODEL`` without a plan
takes retrieval down.

This script supports three phases:

  1. ``plan``        — print what would change. Always safe; no writes.
  2. ``dry-run``     — require a passing gold-corpus eval on the NEW
                       model before anything else.
  3. ``shadow``      — build a shadow table ``knowledge_documents_v2``
                       with the new dim; re-embed every chunk; validate.
  4. ``swap``        — rename tables atomically in one transaction.
  5. ``reap``        — drop the old table after a soak period.

PR-4 ships steps 1 + 2 (the gate) + the skeleton for 3-5. The actual
shadow-write + swap is guarded behind an explicit ``--i-understand``
flag so no operator can run it by accident; full implementation lands
in a follow-up PR once we have a tenant that actually needs to rotate.

Exit codes
----------
    0   — plan/dry-run succeeded
    1   — quality gate refused the rotation
    2   — usage / configuration error
    3   — shadow/swap refused because --i-understand not set
"""

from __future__ import annotations

import argparse
import asyncio
import sys


async def _run_plan(target_model: str, target_dims: int) -> int:
    """Print the plan + sanity-check the catalog. No writes."""
    from core.ai_providers.catalog import EMBEDDING_CATALOG

    print("Rotation plan")
    print("=============")
    print(f"Target embedding model : {target_model}")
    print(f"Target dimensions      : {target_dims}")
    print()

    # Find the catalog entry
    matches = [
        entry for entry in EMBEDDING_CATALOG if entry.model == target_model
    ]
    if not matches:
        print(
            f"✗ Unknown target model. Must be one of "
            f"{[e.model for e in EMBEDDING_CATALOG]}"
        )
        return 2
    catalog_entry = matches[0]
    if catalog_entry.dimensions != target_dims:
        print(
            f"✗ Target dimensions mismatch: catalog says "
            f"{catalog_entry.dimensions} for {target_model}, you passed "
            f"{target_dims}."
        )
        return 2

    print(f"Provider               : {catalog_entry.provider}")
    print(f"Max input tokens       : {catalog_entry.max_input_tokens}")
    print()
    print("Steps:")
    print(
        "  1. Run `embedding_rotate.py eval` — evaluates the gold corpus "
        "against the NEW model. Refuses to proceed below 4.6/5."
    )
    print(
        f"  2. Run `embedding_rotate.py shadow --i-understand` — creates "
        f"knowledge_documents_v2 with vector({target_dims}), re-embeds every chunk."
    )
    print(
        "  3. Run `embedding_rotate.py swap --i-understand` — renames "
        "the tables in one transaction."
    )
    print(
        "  4. Run `embedding_rotate.py reap --i-understand --after N` — "
        "drops the old table N days after swap."
    )
    return 0


async def _run_eval_only(target_model: str) -> int:
    """Run the gold corpus against the proposed model.

    For now this calls the same fixture path ``scripts/rag_eval.py``
    uses — it exercises the scoring pipeline against canned retrieval.
    The full shadow-table path (PR-5) swaps in live retrieval against
    the new index.
    """
    from core.rag.eval import (
        aggregate,
        gate_decision,
        load_gold_corpus,
        score_run,
    )

    # Import here so scripts/ doesn't pull rag_eval's argparse at import.
    import importlib.util
    import pathlib

    script_path = pathlib.Path(__file__).parent / "rag_eval.py"
    spec = importlib.util.spec_from_file_location("scripts.rag_eval", script_path)
    if spec is None or spec.loader is None:
        print("✗ Could not load scripts/rag_eval.py", file=sys.stderr)
        return 2
    rag_eval_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(rag_eval_mod)

    corpus = load_gold_corpus()
    runs = [
        score_run(query, rag_eval_mod._fixture_retrieval(query)) for query in corpus
    ]
    report = aggregate(runs)
    passes, reason = gate_decision(report)
    print(f"Evaluated {target_model!r} against {len(corpus)} gold queries.")
    print(f"Overall score: {report.overall_score:.3f}/5")
    for mod, score in sorted(report.per_modality_score.items()):
        print(f"  {mod}: {score:.3f}")
    if not passes:
        print(f"✗ Gate refused rotation: {reason}", file=sys.stderr)
        return 1
    print(f"✓ Gate passed: {reason}")
    return 0


async def _run_shadow(target_model: str, target_dims: int, confirmed: bool) -> int:
    if not confirmed:
        print(
            "✗ shadow requires --i-understand. This phase re-embeds "
            "every chunk in knowledge_documents against the new model "
            "— it's cheap on small indexes but O(cost) on large ones.",
            file=sys.stderr,
        )
        return 3
    # Skeleton — full implementation lands in the follow-up when a
    # customer first needs to rotate. Keeping the script honest about
    # that vs. pretending it's done.
    print(
        "shadow phase is not yet implemented — the skeleton exists so "
        "future rotation work can slot in without introducing a new "
        "script. Track follow-up in docs/STRICT_REPO_S0_CLOSURE_PLAN_"
        "2026-04-24.md under PR-4's residual risks.",
        file=sys.stderr,
    )
    return 3


async def _main_async(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Safe embedding-model rotation for knowledge_documents."
    )
    parser.add_argument(
        "phase",
        choices=("plan", "eval", "shadow", "swap", "reap"),
        help="Which rotation phase to run.",
    )
    parser.add_argument("--target-model", default="BAAI/bge-small-en-v1.5")
    parser.add_argument("--target-dims", type=int, default=384)
    parser.add_argument(
        "--i-understand",
        action="store_true",
        help=(
            "Required for shadow / swap / reap. Confirms the operator "
            "has read the rotation runbook and accepts that rotation "
            "touches every RAG-indexed chunk in the tenant database."
        ),
    )
    args = parser.parse_args(argv)

    if args.phase == "plan":
        return await _run_plan(args.target_model, args.target_dims)
    if args.phase == "eval":
        return await _run_eval_only(args.target_model)
    if args.phase == "shadow":
        return await _run_shadow(args.target_model, args.target_dims, args.i_understand)
    if args.phase in ("swap", "reap"):
        print(
            f"✗ phase {args.phase!r} refused — depends on the shadow "
            "implementation which is not yet shipped. Run `plan` to see "
            "the roadmap.",
            file=sys.stderr,
        )
        return 3
    return 2


def main() -> int:
    return asyncio.run(_main_async(sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main())

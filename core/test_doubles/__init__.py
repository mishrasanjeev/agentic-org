"""Hermetic test doubles (Foundation #7).

These modules provide deterministic, dependency-free replacements
for external services so PR CI can exercise real code paths
without prod secrets, real network calls, or unpredictable LLM
output.

Each double is wired into the production code through a single
env-var seam — a flag the production code reads at the boundary
to decide between real client and fake. The seams are documented
in ``docs/hermetic_test_doubles.md``.

Currently exposed:

- ``fake_llm`` — deterministic LLM responses keyed by prompt
  fingerprint. Activated via ``AGENTICORG_TEST_FAKE_LLM=1``.
"""

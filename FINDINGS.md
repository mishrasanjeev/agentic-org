# Findings

Defects found while doing other work and deliberately left out of that change.
Each entry says where it was found, what is wrong and what fixing it involves.
Remove an entry in the pull request that fixes it.

## A-1 — Console image base has fixable HIGH advisories in libuuid

- **Found:** container scan of `Dockerfile.ui` (2026-09-14).
- **What:** the pinned `nginx:alpine@sha256:72ba65eb…` base ships util-linux
  `libuuid` 2.42.1-r0, affected by CVE-2026-53612, CVE-2026-53613,
  CVE-2026-53614, CVE-2026-76642, CVE-2026-78408, CVE-2026-78409 and
  CVE-2026-78410 (fixed in 2.42.3-r0 / 2.42.3-r1). The nightly scan only
  covered the API image, so this was not reported before.
- **Fix:** move the digest in `Dockerfile.ui` to an `nginx:alpine` build with
  libuuid 2.42.3-r1 or later (`scripts/refresh_image_digests.sh`), rebuild,
  rescan and drop the seven entries from `.trivyignore.yaml`.

## A-2 — CONTRIBUTING.md overstates the coverage gate

- **Found:** editing `CONTRIBUTING.md` (2026-09-14).
- **What:** "Tests" says a minimum 80% coverage is enforced in CI; CI and
  `scripts/preflight.sh` enforce `--cov-fail-under=55` plus per-module floors
  (`scripts/check_module_coverage.py`).
- **Fix:** state the real gate. The governed-actions work raises it to 75% on
  changed code, so update the text in that change.

## A-3 — Preflight mypy and CI lint type-check different environments

- **Found:** running `scripts/preflight.sh` on `main` at 784cbd03 (2026-09-14).
- **What:** the CI `lint` job installs only `ruff mypy pydantic` before
  `mypy --ignore-missing-imports .`, so third-party packages such as structlog
  are untyped (`Any`) there. Preflight runs the same command inside a full
  `.[dev]` environment, where structlog's types apply, and fails on
  `core/logging_config.py:92` (`list-item`) while CI passes. The script says
  it mirrors CI exactly; it does not.
- **Fix:** type-check against the installed dependencies in both places
  (install `.[dev]` in the lint job) and fix the processor list's annotation,
  or make preflight mirror the lint job's minimal environment. The first
  catches real type errors; the second only restores agreement.

## A-4 — Encrypted-migration gates cannot read JSONB ciphertext containers

- **Found:** re-running `v6z22_case_pseudonym_maps` on a table with rows
  (2026-09-15).
- **What:** `EncryptedMigrationContext.dry_run_decrypt_sample` and
  `assert_decrypt_after` (`core/crypto/migration_helpers.py`) only handle text
  ciphertext. A JSONB column holding `{"_encrypted": "<ciphertext>"}` comes back
  as a `dict` and fails with `AttributeError: 'dict' object has no attribute
  'decode'`, so the gate reports every row as undecryptable.
  `v6z12_voice_runtime` (`voice_calls.transcript_encrypted`) has the same
  shape and cannot be re-run once the table has rows. `v6z22` avoids it by
  skipping the gates when the table already exists.
- **Fix:** unwrap `_encrypted` containers (and the `env1:` envelope prefix) in
  both sampling methods the way `core.crypto.verify_all.parse_encrypted_container`
  does, with a test over a JSONB column.

## A-5 — Key rewrap silently skips JSONB ciphertext outside `*credentials_encrypted`

- **Found:** registering `case_pseudonym_maps.mapping_encrypted` with
  `core/crypto/verify_all.py` (2026-09-15).
- **What:** `core/crypto/rewrap.py::_extract_ciphertext` unwraps
  `{"_encrypted": ...}` only for labels ending in `credentials_encrypted`. For
  `voice_calls.transcript_encrypted` (and now
  `case_pseudonym_maps.mapping_encrypted`) it returns `None`, so those rows are
  never rewrapped and never counted, while `verify_all` still reports their key
  references. Key retirement is blocked (safe), but a rotation cannot complete
  and the rewrap run does not say why. Envelope (`env1:`) values are also not
  handled by the Fernet-only rewrap path.
- **Fix:** unwrap every JSONB `_encrypted` container in `_extract_ciphertext`
  and `_wrap_ciphertext_for_column`, skip or separately handle `env1:` values
  with an explicit count, and add both columns to the rewrap tests.

## A-6 — Some model calls send personal data without redaction

- **Found:** tracing every model caller for pre-model pseudonymisation
  (2026-09-15).
- **What:** with `pseudonymisation.pre_model` off, `core/langgraph/runner.py`
  de-anonymises the run output and trace before `generate_explanation`, which
  sends them to a model (`core/explainer.py`), so the `before_llm` redaction
  mode is undone for that call. Independently of the flag,
  `core/feedback/analyzer.py`, `core/langgraph/sop_parser.py`,
  `core/agent_generator.py`, `core/workflow_generator.py` and the completions in
  `core/agents/marketing/content_factory.py` call models with no redaction.
- **Fix:** pass the masked output and trace to the explainer on the legacy path
  too (as the pseudonymised path now does), and decide per caller whether its
  input can hold personal data; route those through a pseudonymisation session.

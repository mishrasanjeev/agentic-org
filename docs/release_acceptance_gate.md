# Release acceptance gate (Foundation #9)

`scripts/release_acceptance.py` is the composite gate that
fails-closed unless every release criterion from the 2026-04-26
closure plan is green. It blocks the `build → deploy-production`
chain in CI; running it locally before opening a release tag
catches the same failures you'd hit in CI.

## What it checks

| key | criterion | severity | notes |
|-----|-----------|----------|-------|
| `skips` | Every `@pytest.mark.skip / xfail` reason matches the documented allowlist | required | Mirrors Foundation #8's `claude_mistakes` mistake #4. Update the allowlist in BOTH places when adding a new legitimate skip. |
| `coverage` | `scripts/check_module_coverage.py` exits 0 (per-module floors met, real `.coverage` present) | required | Foundation #2 contract. |
| `qa_matrix` | Every P0/P1 row in `docs/qa_test_matrix.yml` has a non-empty `automated_test_ref` | warning | Closure plan acknowledges Foundation #6 (Playwright burndown) is in flight; warning until that lands. |
| `crypto` | `python -m core.crypto.verify_all --check=v1` exits 0 | warning | Needs a real DB; skipped in the static `release-acceptance` CI job and run from `integration-tests` instead. Promote to `required` once the DB feed exists. |
| `tsc` | `cd ui && npx tsc --noEmit` | required | UI type check. |
| `npm_build` | `cd ui && npm run build` | required | Build must produce a dist. |
| `vitest` | `cd ui && npm test -- --run` | required | UI unit tests. |
| `playwright` | `cd ui && npx playwright test` | required | E2E suite. |
| `artefacts` | `coverage.xml` and `docs/qa_test_matrix.yml` exist on disk | warning | Will become `required` once the CI integration job uploads them as artefacts the gate can consume. |

## How it fails

- **Required failure** → exit 1 → blocks `deploy-production` in
  CI, surfaces as `GATE: FAIL` in the printed summary.
- **Warning failure** → still in the report, doesn't break the
  gate. Used for criteria the closure plan acknowledges are in
  flight (qa matrix coverage during Foundation #6 burndown,
  crypto verify-all until the DB feed is wired).

## Local usage

```bash
# Full gate (will fail on missing UI tooling without npm)
python scripts/release_acceptance.py

# Skip UI gates when iterating on backend
python scripts/release_acceptance.py --skip tsc,npm_build,vitest,playwright

# Custom artefact path
python scripts/release_acceptance.py --json /tmp/ra.json
```

The script always writes `release_acceptance.json` (default
filename) with the full per-check breakdown so downstream
tooling can render dashboards or open issues.

## CI integration

The `release-acceptance` job runs after `unit-tests`,
`integration-tests`, and `security-scan` and gates `build`. It
runs the static checks (skips, qa_matrix, artefacts) and lets the
job-specific suites (UI tooling in the build job, crypto
verify-all in integration-tests) own their domain. The
`release_acceptance.json` artefact is uploaded for 30 days for
post-mortem.

## Foundation #8 false-green prevention

Two design choices defend against the "tests didn't run, so
nothing was wrong" pattern:

1. **Missing artefact = failure.** When a check needs a file or
   subprocess output and it's not there, the result is `passed:
   false` with `Missing artefact` in the message. Never silently
   skipped.
2. **Unknown `--skip` keys exit 2** (configuration error)
   instead of 1 (gate failure) so a typo in the CI invocation
   surfaces as a different signal and can't accidentally pass.

## Updating

- New release criterion? Add a `check_<x>` function returning
  `CheckResult`, register it in `CHECK_REGISTRY`, write a
  parametrized smoke test in `tests/regression/test_release_acceptance.py`.
- Promoting a `warning` to `required`? Drop the `severity=` line
  from the `CheckResult`. The default is `required`.
- The skip allowlist lives in BOTH `scripts/release_acceptance.py`
  AND `tests/regression/test_claude_mistakes.py` — keep them in
  sync.

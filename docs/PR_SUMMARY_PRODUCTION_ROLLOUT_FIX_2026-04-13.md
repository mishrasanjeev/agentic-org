# PR Summary: Production Rollout Timeout Fix

## Problem

The `main` deployment workflow failed twice after the CA pack hardening merge:

- run `24348764719`
- rerun of `24348764719`

Both failures happened in `deploy-production` during `Verify production rollout` while waiting on `deployment/agenticorg-api`.

Observed failure:

- `1 old replicas are pending termination...`
- `error: timed out waiting for the condition`

This left the product functionally updated on the QA tenant, but the deployment pipeline itself remained unhealthy and blocked post-deploy jobs.

## Changes

- Bound API shutdown time in `Dockerfile` with:
  - `--timeout-graceful-shutdown 20`
- Made API rollout behavior explicit in `helm/templates/deployment.yaml`:
  - `revisionHistoryLimit: 5`
  - `progressDeadlineSeconds: 900`
  - `minReadySeconds: 10`
  - rolling update strategy with `maxUnavailable: 1` and `maxSurge: 1`
  - `terminationGracePeriodSeconds: 45`
- Hardened `.github/workflows/deploy.yml`:
  - increased rollout verification timeout from `300s` to `600s`
  - added deployment/replicaset/pod/event diagnostics on rollout failure
  - added pod describe/log capture so future failures are actionable from CI logs

## Why This Fix

The failure pattern points to rollout fragility, not a build or unit/integration issue:

- build passed
- unit tests passed
- integration tests passed
- the live tenant already showed the CA behavior after merge
- only the API deployment rollout verification failed

This patch addresses both sides of that problem:

1. the pod should terminate more predictably
2. the rollout controller has more explicit and more forgiving parameters
3. if rollout still fails, CI will now show exactly which pod/deployment state caused it

## Files

- `Dockerfile`
- `helm/templates/deployment.yaml`
- `.github/workflows/deploy.yml`

## Verification

- `helm template agenticorg ./helm -f helm/values-production.yaml`
- result: passed

## Known Limits

- `actionlint` is not installed in this environment, so there was no dedicated workflow linter run
- this patch should be validated by merging and watching the next `main` deploy run

## Post-Merge Check

Confirm on the next `main` run that:

- `deploy-production` succeeds
- `e2e-tests` runs instead of skipping
- `submit-indexing` runs instead of skipping
- no API rollout timeout occurs in `Verify production rollout`

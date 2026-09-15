#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Nightly re-record of model cassettes (.github/workflows/cassette-rerecord.yml).
#
# Re-runs every test that uses the `model_cassette` fixture with
# AGENTICORG_MODEL_MODE=record against a live model, then reports which
# cassettes changed. It reports; it never commits, and a changed cassette is not
# a failure. A failing recording run is.
#
#   MODEL_RECORD_API_KEY   provider key (skips with a warning when empty)
#   MODEL_RECORD_PROVIDER  gemini (default), openai or anthropic
#   REPORT_DIR             where the log and the diff are written (default: .)
set -euo pipefail

summary="${GITHUB_STEP_SUMMARY:-/dev/stdout}"
report_dir="${REPORT_DIR:-.}"
mkdir -p "$report_dir"

if [[ -z "${MODEL_RECORD_API_KEY:-}" ]]; then
  echo "::warning::MODEL_RECORD_API_KEY is not configured; cassettes were not re-recorded"
  printf '## Cassette re-record\n\nSkipped: the `MODEL_RECORD_API_KEY` secret is not configured.\n' >> "$summary"
  exit 0
fi

provider="${MODEL_RECORD_PROVIDER:-gemini}"
case "$provider" in
  gemini) export GOOGLE_GEMINI_API_KEY="$MODEL_RECORD_API_KEY" ;;
  openai) export OPENAI_API_KEY="$MODEL_RECORD_API_KEY" ;;
  anthropic) export ANTHROPIC_API_KEY="$MODEL_RECORD_API_KEY" ;;
  *) echo "rerecord_cassettes: unknown MODEL_RECORD_PROVIDER '$provider'" >&2; exit 2 ;;
esac
unset MODEL_RECORD_API_KEY

export AGENTICORG_ENV=test AGENTICORG_MODEL_MODE=record
export AGENTICORG_SECRET_KEY="${AGENTICORG_SECRET_KEY:-ci-test-secret-key-minimum-16}"

status=0
python -m pytest tests -m model_cassette --no-cov -q -p no:cacheprovider > "$report_dir/rerecord.log" 2>&1 || status=$?
tail -n 20 "$report_dir/rerecord.log"

# pytest exit 5: no test uses the fixture yet.
if [[ "$status" -eq 5 ]]; then
  printf '## Cassette re-record\n\nNo tests use the `model_cassette` fixture yet; nothing to re-record.\n' >> "$summary"
  exit 0
fi

git add --intent-to-add -- tests/cassettes
git diff --stat -- tests/cassettes > "$report_dir/cassette-diff-stat.txt"
git diff -- tests/cassettes > "$report_dir/cassette-diff.patch"
changed="$(git diff --name-only -- tests/cassettes | wc -l | tr -d ' ')"

{
  printf '## Cassette re-record (%s)\n\n' "$provider"
  if [[ "$status" -eq 0 ]]; then
    printf 'Recording run passed.\n\n'
  else
    printf 'Recording run **failed** (pytest exit %s); see `rerecord.log`.\n\n' "$status"
  fi
  if [[ "$changed" -eq 0 ]]; then
    printf 'No cassette changed: recorded behaviour matches the committed cassettes.\n'
  else
    printf '%s cassette file(s) differ from the committed ones. Review `cassette-diff.patch`; re-record in a pull request if the change is expected.\n\n```\n' "$changed"
    cat "$report_dir/cassette-diff-stat.txt"
    printf '```\n'
  fi
} >> "$summary"

exit "$status"

#!/usr/bin/env bash
# SEC-2026-05-P2-009: regenerate the SHA-256 hash list embedded in
# the CSP ``script-src`` directive of ui/nginx.conf and
# ui/nginx.cloudrun.conf.template.
#
# Run after editing any inline ``<script type="application/ld+json">``
# block in ui/index.html. The corresponding regression test
# (tests/regression/test_security_pr_g2_csp_hash_pinning.py) fails if
# the hashes drift, so this script is your fix path.
#
# Usage:
#   bash scripts/refresh_csp_hashes.sh
set -euo pipefail

cd "$(dirname "$0")/.."

INDEX_HTML="ui/index.html"
NGINX_CONF="ui/nginx.conf"
NGINX_TEMPLATE="ui/nginx.cloudrun.conf.template"

if [[ ! -f "$INDEX_HTML" ]]; then
    echo "[refresh_csp_hashes] $INDEX_HTML not found" >&2
    exit 1
fi

echo "[refresh_csp_hashes] computing SHA-256 of inline JSON-LD blocks in $INDEX_HTML"

HASH_LIST=$(python <<'PY'
import hashlib, base64, re, sys
text = open("ui/index.html", encoding="utf-8").read()
# Match the exact body content of every inline JSON-LD script block.
# CSP hashes the bytes BETWEEN the opening and closing tags, exactly
# as the browser sees them.
pattern = re.compile(
    r'<script type="application/ld\+json">\n(.*?)\n  </script>',
    re.DOTALL,
)
hashes = []
for m in pattern.finditer(text):
    content = m.group(1)
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    b64 = base64.b64encode(digest).decode("ascii")
    hashes.append(f"'sha256-{b64}'")
if not hashes:
    print("ERROR: no inline JSON-LD blocks found in ui/index.html", file=sys.stderr)
    sys.exit(1)
print(" ".join(hashes))
PY
)

echo "[refresh_csp_hashes] new hash list:"
echo "  $HASH_LIST"

# In-place rewrite of the script-src clause in both nginx files. The
# pattern below matches everything from "script-src 'self'" up to the
# next non-hash external (https://accounts.google.com) and replaces
# the hash sequence in between.
python <<PY
import re, sys
hash_list = """$HASH_LIST"""

for path in ("$NGINX_CONF", "$NGINX_TEMPLATE"):
    text = open(path, encoding="utf-8").read()
    new_text, n = re.subn(
        r"(script-src 'self')(?: '(?:sha256-[A-Za-z0-9+/=]+|unsafe-inline)')*( https://accounts\.google\.com)",
        rf"\1 {hash_list}\2",
        text,
    )
    if n != 1:
        print(f"ERROR: expected exactly one CSP script-src in {path}, found {n}", file=sys.stderr)
        sys.exit(2)
    open(path, "w", encoding="utf-8").write(new_text)
    print(f"  rewrote {path}")
PY

echo "[refresh_csp_hashes] done. Diff:"
git diff --stat -- "$NGINX_CONF" "$NGINX_TEMPLATE"

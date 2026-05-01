"""PR-G2 regression pins for SEC-009 (CSP unsafe-inline removal).

Closes:

- SEC-2026-05-P2-009 — script-src no longer carries 'unsafe-inline'.
  Each inline ``<script type="application/ld+json">`` block in
  ``ui/index.html`` is whitelisted via a ``'sha256-...'`` token in
  the CSP. If a contributor edits a JSON-LD block, this test fails
  with the new hash they need to add (run
  ``bash scripts/refresh_csp_hashes.sh`` to regenerate).

style-src retains 'unsafe-inline' on purpose (Tailwind utility class
output generates inline styles; the residual XSS impact via styles
alone is materially smaller than via scripts).
"""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"
NGINX_CONFIGS = (
    REPO_ROOT / "ui" / "nginx.conf",
    REPO_ROOT / "ui" / "nginx.cloudrun.conf.template",
)

_INLINE_JSON_LD = re.compile(
    r'<script type="application/ld\+json">\n(.*?)\n  </script>',
    re.DOTALL,
)


def _compute_inline_script_hashes(html: str) -> list[str]:
    hashes: list[str] = []
    for m in _INLINE_JSON_LD.finditer(html):
        content = m.group(1)
        digest = hashlib.sha256(content.encode("utf-8")).digest()
        hashes.append("sha256-" + base64.b64encode(digest).decode("ascii"))
    return hashes


def _extract_csp_header(text: str) -> str:
    for line in text.splitlines():
        if "Content-Security-Policy" in line:
            return line
    pytest.fail("Content-Security-Policy header not found in nginx config")
    return ""  # unreachable


def _extract_script_src_tokens(csp_line: str) -> list[str]:
    """Return the list of script-src tokens (single-quoted)."""
    m = re.search(r"script-src\s+([^;]*);", csp_line)
    if not m:
        pytest.fail("script-src directive not found in CSP")
    return re.findall(r"'([^']*)'", m.group(1))


@pytest.mark.parametrize("config", NGINX_CONFIGS, ids=lambda p: p.name)
def test_sec_009_script_src_does_not_allow_unsafe_inline(config: Path) -> None:
    """``script-src`` must not contain ``'unsafe-inline'`` — that's
    the SEC-009 fix. Hashes replace it."""
    csp = _extract_csp_header(config.read_text(encoding="utf-8"))
    tokens = _extract_script_src_tokens(csp)
    assert "unsafe-inline" not in tokens, (
        f"{config.name}: script-src must not contain 'unsafe-inline'. "
        "The PR-G2 fix replaces it with sha256 hashes of the inline "
        "JSON-LD blocks. Run scripts/refresh_csp_hashes.sh if you've "
        "edited ui/index.html."
    )


@pytest.mark.parametrize("config", NGINX_CONFIGS, ids=lambda p: p.name)
def test_sec_009_script_src_does_not_allow_unsafe_eval(config: Path) -> None:
    csp = _extract_csp_header(config.read_text(encoding="utf-8"))
    tokens = _extract_script_src_tokens(csp)
    assert "unsafe-eval" not in tokens, (
        f"{config.name}: script-src must not contain 'unsafe-eval'."
    )


@pytest.mark.parametrize("config", NGINX_CONFIGS, ids=lambda p: p.name)
def test_sec_009_script_src_includes_every_inline_jsonld_hash(
    config: Path,
) -> None:
    """Every inline JSON-LD block in ``ui/index.html`` must have a
    matching ``'sha256-...'`` token in the script-src directive. If
    this fails, the source HTML drifted from the pinned hashes — run
    ``bash scripts/refresh_csp_hashes.sh`` to regenerate.
    """
    expected = _compute_inline_script_hashes(
        INDEX_HTML.read_text(encoding="utf-8")
    )
    assert expected, "no inline JSON-LD blocks found in ui/index.html"
    csp = _extract_csp_header(config.read_text(encoding="utf-8"))
    tokens = set(_extract_script_src_tokens(csp))
    missing = [h for h in expected if h not in tokens]
    assert not missing, (
        f"{config.name}: the following CSP hashes are missing — the "
        "inline JSON-LD content drifted. Run "
        "scripts/refresh_csp_hashes.sh to fix:\n  "
        + "\n  ".join(f"'{h}'" for h in missing)
    )


def test_sec_009_nginx_configs_have_identical_script_src() -> None:
    """The two nginx configs (local + Cloud Run template) must carry
    the same script-src directive so the deployed CSP doesn't drift
    between environments."""
    sources = [
        _extract_script_src_tokens(_extract_csp_header(p.read_text(encoding="utf-8")))
        for p in NGINX_CONFIGS
    ]
    a, b = sources
    assert sorted(a) == sorted(b), (
        "nginx.conf and nginx.cloudrun.conf.template have diverging "
        "script-src directives. They must stay aligned — run "
        "scripts/refresh_csp_hashes.sh after any edit."
    )


def test_sec_009_refresh_script_exists_and_documented() -> None:
    """The hash-refresh helper must exist + describe the workflow."""
    script = REPO_ROOT / "scripts" / "refresh_csp_hashes.sh"
    assert script.exists(), "scripts/refresh_csp_hashes.sh missing"
    text = script.read_text(encoding="utf-8")
    assert "JSON-LD" in text or "json-ld" in text or "json+ld" in text
    assert "ui/index.html" in text


def test_sec_009_csp_does_not_have_wildcard_in_script_src() -> None:
    for config in NGINX_CONFIGS:
        csp = _extract_csp_header(config.read_text(encoding="utf-8"))
        m = re.search(r"script-src\s+([^;]*);", csp)
        assert m, f"{config.name}: script-src directive not found"
        assert " * " not in (" " + m.group(1) + " "), (
            f"{config.name}: script-src must not include a wildcard."
        )

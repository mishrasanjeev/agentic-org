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
ANALYTICS_COMPONENT = REPO_ROOT / "ui" / "src" / "components" / "Analytics.tsx"

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
    match = re.search(r'add_header\s+Content-Security-Policy\s+"([^"]+)"', text)
    if not match:
        pytest.fail("Content-Security-Policy header not found in nginx config")
    csp = match.group(1)
    variables = dict(
        re.findall(r'set\s+\$(csp_jsonld_hashes_\d+)\s+"([^"]*)";', text)
    )
    csp = re.sub(
        r"\$(csp_jsonld_hashes_\d+)",
        lambda match: variables.get(match.group(1), match.group(0)),
        csp,
    )
    assert "$csp_jsonld_hashes_" not in csp, (
        "Content-Security-Policy references an undefined JSON-LD hash variable"
    )
    return csp


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


@pytest.mark.parametrize("config", NGINX_CONFIGS, ids=lambda p: p.name)
def test_sec_009_csp_parameters_fit_nginx_parser_buffer(config: Path) -> None:
    text = config.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert all(len(line.encode("utf-8")) < 4096 for line in lines), (
        f"{config.name}: nginx configuration parameters must stay below "
        "the 4096-byte parser buffer"
    )
    csp_line = next(
        line for line in lines if "Content-Security-Policy" in line
    )
    assert "$csp_jsonld_hashes_" in csp_line
    chunks = re.findall(
        r'set\s+\$csp_jsonld_hashes_\d+\s+"([^"]*)";', text
    )
    assert chunks, f"{config.name}: generated CSP hash chunks are missing"
    assert all(len(chunk) <= 2800 for chunk in chunks)


@pytest.mark.parametrize("config", NGINX_CONFIGS, ids=lambda p: p.name)
def test_sec_009_location_headers_merge_server_security_headers(config: Path) -> None:
    """Location cache headers must not suppress the server-level CSP."""
    text = config.read_text(encoding="utf-8")
    assert "add_header_inherit merge;" in text, (
        f"{config.name}: locations with add_header directives must merge the "
        "server-level security headers"
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


def test_sec_009_analytics_bootstrap_does_not_inject_inline_script() -> None:
    """GA can run under strict CSP without runtime inline script tags.

    The production CSP hashes only static JSON-LD blocks from index.html.
    Adding a runtime ``script.textContent`` bootstrap creates unpinned inline
    script and reopens the browser console CSP violation caught by Playwright.
    """
    text = ANALYTICS_COMPONENT.read_text(encoding="utf-8")
    forbidden = (
        ".textContent",
        "innerHTML",
        "appendChild(inline",
    )
    missing = [pattern for pattern in forbidden if pattern in text]
    assert not missing, (
        "Analytics bootstrap must not create runtime inline scripts under "
        f"strict CSP; found {', '.join(missing)}"
    )
    assert "window.gtag" in text and "window.dataLayer" in text

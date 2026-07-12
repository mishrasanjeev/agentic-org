"""Regression coverage for registry-driven nginx noindex headers."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_SITE = ROOT / "ui" / "src" / "content" / "publicSite.json"
NGINX_CONFIGS = (
    ROOT / "ui" / "nginx.conf",
    ROOT / "ui" / "nginx.cloudrun.conf.template",
)


def _noindex_patterns(config: Path) -> list[re.Pattern[str]]:
    text = config.read_text(encoding="utf-8")
    map_block = re.search(
        r"map\s+\$request_uri\s+\$seo_robots_tag\s*\{(.*?)^\}",
        text,
        re.DOTALL | re.MULTILINE,
    )
    assert map_block, f"{config.name}: $seo_robots_tag map is missing"
    patterns = [
        re.compile(pattern)
        for pattern in re.findall(
            r'^\s*~(\S+)\s+"noindex,\s*nofollow";\s*$',
            map_block.group(1),
            re.MULTILINE,
        )
    ]
    assert patterns, f"{config.name}: no noindex map patterns found"
    return patterns


def _matches(patterns: list[re.Pattern[str]], uri: str) -> bool:
    return any(pattern.search(uri) for pattern in patterns)


def _registered_pages() -> list[dict[str, object]]:
    return json.loads(PUBLIC_SITE.read_text(encoding="utf-8"))["pages"]


@pytest.mark.parametrize("config", NGINX_CONFIGS, ids=lambda path: path.name)
def test_every_registered_noindex_route_maps_to_response_header(config: Path) -> None:
    patterns = _noindex_patterns(config)
    noindex_pages = [page for page in _registered_pages() if page["index"] is False]
    assert noindex_pages, "public route registry must contain noindex pages"

    for page in noindex_pages:
        path = str(page["path"])
        for uri in (path, path + "/", path + "?utm_source=test", path + "/?utm_source=test"):
            assert _matches(patterns, uri), (
                f"{config.name}: registered noindex route variant is uncovered: {uri}"
            )


@pytest.mark.parametrize("config", NGINX_CONFIGS, ids=lambda path: path.name)
def test_campaign_rules_cover_subpaths_without_overmatching_siblings(config: Path) -> None:
    patterns = _noindex_patterns(config)
    campaigns = [
        str(page["path"])
        for page in _registered_pages()
        if page["index"] is False and str(page["path"]).startswith("/solutions/")
    ]
    assert campaigns == [
        "/solutions/ai-invoice-processing",
        "/solutions/automated-bank-reconciliation",
        "/solutions/payroll-automation",
    ]

    for path in campaigns:
        assert _matches(patterns, path + "/confirmation?lead=accepted")
        assert not _matches(patterns, path + "-guide")


@pytest.mark.parametrize("config", NGINX_CONFIGS, ids=lambda path: path.name)
def test_existing_auth_and_dashboard_match_boundaries_are_preserved(config: Path) -> None:
    patterns = _noindex_patterns(config)
    for uri in (
        "/dashboard",
        "/dashboard/agents/agent-1",
        "/dashboard?tab=agents",
        "/login",
        "/login/?next=%2Fdashboard",
        "/sso/callback?code=redacted",
    ):
        assert _matches(patterns, uri), f"{config.name}: private URI is uncovered: {uri}"

    assert not _matches(patterns, "/dashboarding")
    assert not _matches(patterns, "/login/help")
    assert not _matches(patterns, "/solutions/cfo")


@pytest.mark.parametrize("config", NGINX_CONFIGS, ids=lambda path: path.name)
def test_registered_indexable_routes_do_not_receive_noindex(config: Path) -> None:
    patterns = _noindex_patterns(config)
    for page in _registered_pages():
        if page["index"] is not False:
            path = str(page["path"])
            assert not _matches(patterns, path)
            assert not _matches(patterns, path + "?utm_source=test")

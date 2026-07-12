"""Contract tests for the public SEO/AEO content registry."""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.billing.limits import PLAN_PRICING

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_SITE_PATH = ROOT / "ui" / "src" / "content" / "publicSite.json"
INDEX_PATH = ROOT / "ui" / "index.html"


def _load_public_site() -> dict:
    return json.loads(PUBLIC_SITE_PATH.read_text(encoding="utf-8"))


def _normalized_runs(value: object) -> int | str:
    if str(value).lower() == "unlimited":
        return "Unlimited"
    digits = re.sub(r"\D", "", str(value))
    return int(digits)


def test_public_pricing_matches_backend_billing_contract() -> None:
    public = _load_public_site()
    public_by_id = {plan["id"]: plan for plan in public["plans"]}
    backend_by_id = {plan["plan"]: plan for plan in PLAN_PRICING}

    assert public_by_id.keys() == backend_by_id.keys()
    for plan_id, backend in backend_by_id.items():
        page = public_by_id[plan_id]
        assert page["name"] == backend["label"]
        assert page["priceUsd"] == backend["price_usd"]
        assert page["priceInr"] == backend["price_inr"]
        assert str(page["agents"]) == str(backend["agents"])
        assert _normalized_runs(page["runs"]) == _normalized_runs(backend["runs"])
        assert page["storage"] == backend["storage"]


def test_indexable_pages_have_unique_answer_ready_metadata() -> None:
    public = _load_public_site()
    indexed = [page for page in public["pages"] if page["index"]]

    paths = [page["path"] for page in public["pages"]]
    titles = [page["title"] for page in indexed]
    descriptions = [page["description"] for page in indexed]

    assert len(paths) == len(set(paths))
    assert len(titles) == len(set(titles))
    assert len(descriptions) == len(set(descriptions))

    for page in indexed:
        assert page["path"].startswith("/")
        assert 20 <= len(page["title"]) <= 65
        assert 120 <= len(page["description"]) <= 170
        assert len(page["summary"]) >= 100
        assert page["primaryQuestion"].endswith("?")
        assert len(page["keywords"]) >= 3

    known_paths = set(paths)
    for alias, canonical in public["aliases"].items():
        assert alias.startswith("/")
        assert canonical in known_paths
        assert alias != canonical


def test_landing_faqs_are_substantive_and_unique() -> None:
    faqs = _load_public_site()["landingFaqs"]
    questions = [item["question"] for item in faqs]

    assert len(faqs) >= 6
    assert len(questions) == len(set(questions))
    assert all(question.endswith("?") for question in questions)
    assert all(len(item["answer"]) >= 120 for item in faqs)


def test_root_structured_data_excludes_unverified_rich_result_claims() -> None:
    index = INDEX_PATH.read_text(encoding="utf-8")

    assert '"aggregateRating"' not in index
    assert '"SearchAction"' not in index
    assert '"SoftwareCompany"' not in index
    assert '"FAQPage"' not in index
    assert '"BreadcrumbList"' not in index
    assert "1000+ Integrations" not in index

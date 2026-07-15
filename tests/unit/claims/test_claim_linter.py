from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from core.claims.linter import discover_public_surfaces, scan_surfaces
from core.claims.schema import ClaimRegistryDocument

NOW = datetime(2026, 7, 14, 12, tzinfo=UTC)


def _document(*, illustrative: bool = False) -> ClaimRegistryDocument:
    evidence_ids = [] if illustrative else ["EVID-MKT-C01"]
    treatment = "Illustrative" if illustrative else "EvidenceBacked"
    required_state = None if illustrative else "Integrated"
    approved = "Cut close time by 30%." if illustrative else "Campaign pilot increased qualified pipeline by 12%."
    return ClaimRegistryDocument.model_validate(
        {
            "schema_version": "agenticorg.claim-registry.v1",
            "registry_version": "test.1",
            "product_version": "4.8.0",
            "generated_at": "2026-07-13T00:00:00Z",
            "capabilities": [
                {
                    "capability_id": "MKT-C01",
                    "domain": "Marketing",
                    "title": "Campaign operations",
                    "maturity": "Integrated",
                    "gate_result": "Passed",
                    "public_availability": "Beta",
                    "claim_treatment": treatment,
                    "owner": "marketing-owner",
                    "expires_at": "2026-08-01T00:00:00Z",
                    "evidence_ids": evidence_ids,
                    "permitted_claim_ids": ["MKT-CLAIM-PIPELINE"],
                    "limitations": [],
                }
            ],
            "evidence": []
            if illustrative
            else [
                {
                    "evidence_id": "EVID-MKT-C01",
                    "capability_ids": ["MKT-C01"],
                    "uri": "artifact://pilot/mkt-c01.json",
                    "checksum": "sha256:" + "a" * 64,
                    "environment": "vendor_sandbox",
                    "provider_account_class": "controlled-test-account",
                    "product_version": "4.8.0",
                    "commit_sha": "b" * 40,
                    "executed_at": "2026-07-01T00:00:00Z",
                    "state": "Integrated",
                    "result": "Passed",
                    "reviewer": "release-reviewer",
                    "expires_at": "2026-08-01T00:00:00Z",
                }
            ],
            "claims": [
                {
                    "claim_id": "MKT-CLAIM-PIPELINE",
                    "kind": "outcome",
                    "treatment": treatment,
                    "approved_text": [approved],
                    "capability_ids": ["MKT-C01"],
                    "evidence_ids": evidence_ids,
                    "required_evidence_state": required_state,
                    "asserted_availability": None,
                    "owner": "marketing-owner",
                    "approver": "marketing-approver",
                    "product_version": "4.8.0",
                    "expires_at": "2026-08-01T00:00:00Z",
                    "surfaces": ["README.md"],
                    "inventory_source": None,
                    "limitations": [],
                }
            ],
        }
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_registered_approved_claim_passes(tmp_path: Path) -> None:
    _write(
        tmp_path / "README.md",
        "<!-- claim-id: MKT-CLAIM-PIPELINE -->\nCampaign pilot increased qualified pipeline by 12%.\n",
    )
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    assert report.valid, report.issues


def test_unmarked_outcome_claim_is_rejected_without_hardcoded_copy(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "Customers reduce close time by 30%.\n")
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    assert "unbound_public_claim" in {issue.code for issue in report.issues}


@pytest.mark.parametrize(
    "copy",
    [
        "Automated reconciliation reached a 99.7% auto-match rate.",
        "The screening agent processes 500 resumes/hour.",
        "The pilot had zero errors in 6 months.",
        "Close completed in 2 days, not 30.",
        "Deploy in under 5 min.",
        "Review cycle moved from 5d to 18h.",
        "The worksheet reports ₹69,800/month savings.",
        "Observed 4-hour MTTR.",
        "Measured <1ms policy overhead.",
        "Audit logs have 7-year retention.",
        "The catalog includes 430 tools.",
        "100% of critical decisions are automatically covered.",
        "The plan price is $49/month.",
        "Pro includes 15 agents and 10,000 runs/month.",
        "Custom agents are unlimited.",
        "INR 4,999/month per client.",
        "Start Free.",
        "Create a free account.",
        "Get an API key - Free.",
        "Start a 14-day free trial.",
        "Save 20% with an annual discount.",
        "Enterprise includes 24/7 support and a custom SLA.",
        "Our uptime target is 99.9%.",
        "Customers may request service credits equal to qualifying downtime.",
        "Customer data is stored in Singapore.",
        "Support responds within one business day.",
        "Refunds are processed within 7 business days.",
        "Messages go out as the founder; never reveal that this is AI.",
    ],
)
def test_unmarked_hard_claim_shapes_are_rejected(tmp_path: Path, copy: str) -> None:
    _write(tmp_path / "README.md", copy + "\n")
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    assert "unbound_public_claim" in {issue.code for issue in report.issues}, copy


@pytest.mark.parametrize(
    "copy",
    [
        "Example confidence floor: 88%.",
        "Configured match tolerance: 2%.",
        "Heartbeat every 30 sec.",
        "Invoices above ₹5L require CFO approval within 4h.",
        "This repository is not SOC 2 certified; certification is pending.",
        "We do not claim a 99.7% match rate; evidence is pending.",
        "This template is not production-ready.",
        "The animation transition duration is 500ms.",
        "/* ROI Calculator — Interactive savings estimator */",
        'result: line.color || "text-slate-300 text-sm"',
        "Saved agent definitions require review.",
        "Growth Lead",
        "This inventory is not evidence that every connector is production-proven.",
        "Optional tools are not configured or certified native connectors.",
        "These are not measured customer results or guaranteed savings.",
        "No improvement is assumed.",
        "This is not a quote, guarantee, benchmark, savings projection, or purchasing advice.",
        "Confirm prices and limits in the billing catalog before purchase.",
        "This page does not assume an annual discount.",
        "Do you offer annual billing?",
        "Apache 2.0 source code is free for commercial use.",
        "Service credits, support response objectives, refunds, retention, and residency "
        "are governed by the signed order form.",
        "Do not impersonate a founder or conceal AI assistance.",
        "These controls do not by themselves confer SOC 2 or regulatory compliance.",
        "Product features alone do not establish readiness or certification.",
        "This page makes no certification or audit-status claim.",
        "Manage templates without treating them as certified behavior.",
        "SOC 2 for AI Systems: Educational Controls to Evaluate.",
        "A cutoff checklist requires recovery procedures and improvement-plan review.",
    ],
)
def test_configuration_and_explicit_denial_copy_is_not_a_claim(tmp_path: Path, copy: str) -> None:
    _write(tmp_path / "README.md", copy + "\n")
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    assert report.valid, (copy, report.issues)


@pytest.mark.parametrize(
    "copy",
    [
        'slug: "soc2-ai-compliance",',
        'relatedSlugs: ["soc2-ai-compliance", "ai-audit-trail"],',
        'keywords: ["SOC 2 AI controls", "compliance audit"],',
    ],
)
def test_source_only_fields_are_not_visible_claims(tmp_path: Path, copy: str) -> None:
    _write(tmp_path / "README.md", copy + "\n")
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    assert report.valid, (copy, report.issues)


@pytest.mark.parametrize(
    "comment",
    [
        "<!--\nObserved uptime was 99.9%.\n-->",
        "{/*\nObserved uptime was 99.9%.\n*/}",
    ],
)
def test_multiline_source_comments_are_not_visible_claims(
    tmp_path: Path,
    comment: str,
) -> None:
    _write(tmp_path / "README.md", comment + "\nEvidence is required before publication.\n")
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    assert report.valid, report.issues


def test_single_line_structured_value_does_not_absorb_following_copy(tmp_path: Path) -> None:
    _write(
        tmp_path / "README.md",
        'const stat = { value: "0", label: "Paid-state claims from cache" };\n'
        'const body = "Save tenant configuration for later review.";\n',
    )
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    assert report.valid, report.issues


def test_explicit_certification_denial_does_not_hide_performance_assertion(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "Not SOC 2 certified; observed uptime was 99.9%.\n")
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    claims = [issue for issue in report.issues if issue.code == "unbound_public_claim"]
    assert len(claims) == 1
    assert claims[0].message.startswith("performance claim")


def test_structured_multiline_metric_is_rejected(tmp_path: Path) -> None:
    _write(
        tmp_path / "README.md",
        'const metric = {\n  value: "100%",\n  label: "Accuracy",\n  description: "Zero false positives",\n};\n',
    )
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    claims = [issue for issue in report.issues if issue.code == "unbound_public_claim"]
    assert len(claims) == 1
    assert claims[0].line == 2


def test_structured_multiline_configuration_metric_is_allowed(tmp_path: Path) -> None:
    _write(
        tmp_path / "README.md",
        'const policy = {\n  value: "88%",\n'
        '  label: "Confidence floor",\n'
        '  description: "Example approval threshold",\n};\n',
    )
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    assert report.valid, report.issues


def test_marker_does_not_authorize_changed_copy(tmp_path: Path) -> None:
    _write(
        tmp_path / "README.md",
        "<!-- claim-id: MKT-CLAIM-PIPELINE -->\nCampaigns guarantee 99% revenue growth.\n",
    )
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    codes = {issue.code for issue in report.issues}
    assert "claim_text_not_approved" in codes
    assert "unbound_public_claim" in codes


def test_unknown_marker_does_not_hide_following_claim(tmp_path: Path) -> None:
    _write(tmp_path / "README.md", "<!-- claim-id: UNKNOWN-CLAIM -->\nObserved uptime was 99.9%.\n")
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    codes = {issue.code for issue in report.issues}
    assert "claim_not_registered" in codes
    assert "unbound_public_claim" in codes


def test_approved_marker_binds_only_approved_text_span(tmp_path: Path) -> None:
    _write(
        tmp_path / "README.md",
        "<!-- claim-id: MKT-CLAIM-PIPELINE -->\n"
        "Campaign pilot increased qualified pipeline by 12%.\n"
        "Observed uptime was 99.9%.\n",
    )
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    claims = [issue for issue in report.issues if issue.code == "unbound_public_claim"]
    assert len(claims) == 1
    assert claims[0].line == 3


def test_approved_marker_does_not_hide_same_line_residual_claim(tmp_path: Path) -> None:
    _write(
        tmp_path / "README.md",
        "<!-- claim-id: MKT-CLAIM-PIPELINE --> "
        "Campaign pilot increased qualified pipeline by 12%. Observed uptime was 99.9%.\n",
    )
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    claims = [issue for issue in report.issues if issue.code == "unbound_public_claim"]
    assert len(claims) == 1
    assert claims[0].line == 1


@pytest.mark.parametrize(
    "copy",
    [
        "Marketing says Campaign pilot increased qualified pipeline by 12%.",
        "Campaign pilot increased qualified pipeline by 12%. Terms may differ.",
    ],
)
def test_approved_marker_requires_exact_complete_visible_text(tmp_path: Path, copy: str) -> None:
    _write(tmp_path / "README.md", f"<!-- claim-id: MKT-CLAIM-PIPELINE -->\n{copy}\n")
    report = scan_surfaces(tmp_path, _document(), paths=["README.md"], now=NOW)
    assert "claim_text_not_approved" in {issue.code for issue in report.issues}


def test_illustrative_claim_requires_visible_label(tmp_path: Path) -> None:
    _write(
        tmp_path / "README.md",
        "<!-- claim-id: MKT-CLAIM-PIPELINE -->\nCut close time by 30%.\n",
    )
    report = scan_surfaces(tmp_path, _document(illustrative=True), paths=["README.md"], now=NOW)
    assert "illustrative_claim_not_labeled" in {issue.code for issue in report.issues}


def test_default_discovery_covers_requested_surface_classes(tmp_path: Path) -> None:
    for relative in (
        "README.md",
        "SECURITY.md",
        "pyproject.toml",
        "sdk/README.md",
        "mcp-server/README.md",
        "mcp-server/package.json",
        "mcp-server/server.json",
        "mcp-server/src/index.ts",
        "core/agents/prompts/sales_agent.prompt.txt",
        "core/reports/generator.py",
        "core/seed_ca_demo.py",
        "ui/index.html",
        "ui/public/llms.txt",
        "ui/public/llms-full.txt",
        "ui/public/manifest.json",
        "ui/dist/llms.txt",
        "ui/dist/llms-full.txt",
        "ui/nginx.conf",
        "ui/nginx.cloudrun.conf.template",
        "ui/src/pages/Landing.tsx",
        "ui/src/pages/Pricing.tsx",
        "ui/src/pages/CFOSolution.tsx",
        "ui/src/pages/ads/AdsLanding.tsx",
        "ui/src/pages/blog/blogData.ts",
        "ui/src/pages/resources/contentData.ts",
        "ui/src/pages/HowGrantexWorks.tsx",
        "ui/src/pages/IntegrationWorkflow.tsx",
        "ui/src/pages/OpenAgenticCommerceProtocol.tsx",
        "ui/src/pages/Status.tsx",
        "ui/src/pages/legal/Terms.tsx",
        "ui/src/pages/legal/Privacy.tsx",
        "ui/src/pages/legal/Support.tsx",
        "ui/src/pages/legal/Refund.tsx",
        "ui/src/components/AgentActivityTicker.tsx",
        "ui/src/components/AgentsInAction.tsx",
        "ui/src/components/InteractiveDemo.tsx",
        "ui/src/components/ROICalculator.tsx",
        "ui/src/components/SocialProof.tsx",
        "ui/src/components/WorkflowAnimation.tsx",
    ):
        _write(tmp_path / relative, "plain copy\n")
    _write(tmp_path / "ui/src/pages/Settings.tsx", "not a governed default surface\n")
    found = {path.relative_to(tmp_path).as_posix() for path in discover_public_surfaces(tmp_path)}
    assert found == {
        "README.md",
        "SECURITY.md",
        "pyproject.toml",
        "sdk/README.md",
        "mcp-server/README.md",
        "mcp-server/package.json",
        "mcp-server/server.json",
        "mcp-server/src/index.ts",
        "core/agents/prompts/sales_agent.prompt.txt",
        "core/reports/generator.py",
        "core/seed_ca_demo.py",
        "ui/index.html",
        "ui/public/llms.txt",
        "ui/public/llms-full.txt",
        "ui/public/manifest.json",
        "ui/dist/llms.txt",
        "ui/dist/llms-full.txt",
        "ui/nginx.conf",
        "ui/nginx.cloudrun.conf.template",
        "ui/src/pages/Landing.tsx",
        "ui/src/pages/Pricing.tsx",
        "ui/src/pages/CFOSolution.tsx",
        "ui/src/pages/ads/AdsLanding.tsx",
        "ui/src/pages/blog/blogData.ts",
        "ui/src/pages/resources/contentData.ts",
        "ui/src/pages/HowGrantexWorks.tsx",
        "ui/src/pages/IntegrationWorkflow.tsx",
        "ui/src/pages/OpenAgenticCommerceProtocol.tsx",
        "ui/src/pages/Status.tsx",
        "ui/src/pages/legal/Terms.tsx",
        "ui/src/pages/legal/Privacy.tsx",
        "ui/src/pages/legal/Support.tsx",
        "ui/src/pages/legal/Refund.tsx",
        "ui/src/components/AgentActivityTicker.tsx",
        "ui/src/components/AgentsInAction.tsx",
        "ui/src/components/InteractiveDemo.tsx",
        "ui/src/components/ROICalculator.tsx",
        "ui/src/components/SocialProof.tsx",
        "ui/src/components/WorkflowAnimation.tsx",
    }

"""Workflow template catalog — single source of truth for the
"Templates" tab on the Workflows page.

Before PR-C3 this catalog lived as a hardcoded 21-entry array in
`ui/src/pages/Workflows.tsx`. That meant adding or renaming a template
required a UI code change and a deploy. The catalog is now served by
`GET /api/v1/workflows/templates` and the UI consumes it.

Each template here is metadata only — name, description, domain, step
count, trigger kind. The actual workflow graph is produced when the
user picks a template and the UI routes to the Create Workflow page
with the template id.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WorkflowTemplate:
    id: str
    name: str
    description: str
    domain: str  # finance | hr | marketing | ops
    steps: int
    trigger: str  # schedule | api_event | manual | email_received


TEMPLATES: list[WorkflowTemplate] = [
    # Finance
    WorkflowTemplate("tpl-invoice-processing", "Invoice Processing",
                     "Automatically extract, validate, and route invoices for approval based on amount thresholds.",
                     "finance", 5, "api_event"),
    WorkflowTemplate("tpl-bank-reconciliation", "Bank Reconciliation",
                     "Match bank statement entries with ledger transactions and flag discrepancies for review.",
                     "finance", 4, "schedule"),
    WorkflowTemplate("tpl-month-end-close", "Month-End Close",
                     "Orchestrate journal entries, accruals, reconciliations, and reporting for month-end close.",
                     "finance", 8, "schedule"),
    WorkflowTemplate("tpl-gst-filing", "GST Filing",
                     "Collect sales and purchase data, compute GST liability, and prepare GSTR-1/3B filings.",
                     "finance", 6, "schedule"),
    WorkflowTemplate("tpl-expense-approval", "Expense Approval",
                     "Route expense reports through policy checks and multi-level approval chains.",
                     "finance", 4, "api_event"),
    # HR
    WorkflowTemplate("tpl-payroll-processing", "Payroll Processing",
                     "Calculate salaries, deductions, taxes, and generate payslips for all employees.",
                     "hr", 6, "schedule"),
    WorkflowTemplate("tpl-employee-onboarding", "Employee Onboarding",
                     "Provision accounts, assign equipment, schedule orientation, and notify stakeholders in parallel.",
                     "hr", 7, "api_event"),
    WorkflowTemplate("tpl-leave-approval", "Leave Approval",
                     "Validate leave balance, check team coverage, and route to manager for approval.",
                     "hr", 3, "api_event"),
    WorkflowTemplate("tpl-performance-review", "Performance Review Cycle",
                     "Initiate self-assessments, collect manager ratings, calibrate scores, and finalize reviews.",
                     "hr", 6, "schedule"),
    WorkflowTemplate("tpl-talent-screening", "Talent Screening",
                     "Parse resumes, score candidates against job requirements, and shortlist for interviews.",
                     "hr", 5, "api_event"),
    # Marketing
    WorkflowTemplate("tpl-campaign-launch", "Campaign Launch",
                     "Coordinate creative assets, audience targeting, channel setup, and launch across platforms.",
                     "marketing", 6, "manual"),
    WorkflowTemplate("tpl-lead-scoring", "Lead Scoring",
                     "Evaluate inbound leads using firmographic, behavioral, and engagement signals.",
                     "marketing", 4, "api_event"),
    WorkflowTemplate("tpl-content-publishing", "Content Publishing",
                     "Draft, review, optimize for SEO, and publish content across blog and social channels.",
                     "marketing", 5, "manual"),
    WorkflowTemplate("tpl-social-media-calendar", "Social Media Calendar",
                     "Plan, schedule, and auto-publish posts across social media platforms on a weekly cadence.",
                     "marketing", 4, "schedule"),
    WorkflowTemplate("tpl-email-drip-campaign", "Email Drip Campaign",
                     "Enroll contacts, send sequenced emails, track engagement, and branch based on actions.",
                     "marketing", 5, "api_event"),
    # Ops
    WorkflowTemplate("tpl-support-ticket-triage", "Support Ticket Triage",
                     "Classify incoming tickets by urgency and topic, then route to the appropriate support tier.",
                     "ops", 4, "api_event"),
    WorkflowTemplate("tpl-it-asset-provisioning", "IT Asset Provisioning",
                     "Allocate laptops, software licenses, and cloud accounts for new hires or role changes.",
                     "ops", 5, "api_event"),
    WorkflowTemplate("tpl-vendor-onboarding", "Vendor Onboarding",
                     "Collect vendor documents, verify compliance, set up payment details, and approve registration.",
                     "ops", 5, "manual"),
    WorkflowTemplate("tpl-contract-renewal", "Contract Renewal",
                     "Track contract expiry dates, notify stakeholders, negotiate terms, and execute renewals.",
                     "ops", 5, "schedule"),
    WorkflowTemplate("tpl-compliance-audit", "Compliance Audit",
                     "Gather evidence, run control checks, flag exceptions, and generate audit reports.",
                     "ops", 6, "schedule"),
    WorkflowTemplate("tpl-report-generation", "Report Generation",
                     "Aggregate data from multiple sources, generate formatted reports, "
                     "and distribute to stakeholders.",
                     "ops", 4, "schedule"),
]


def list_templates(domain: str | None = None) -> list[dict]:
    """Return the catalog, optionally filtered by domain."""
    items = [t for t in TEMPLATES if domain is None or t.domain == domain]
    return [
        {
            "id": t.id,
            "name": t.name,
            "description": t.description,
            "domain": t.domain,
            "steps": t.steps,
            "trigger": t.trigger,
        }
        for t in items
    ]

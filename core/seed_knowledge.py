"""Seed the knowledge base with real CA/GST/compliance documents.

Downloads publicly available documents from government sources and
stores them as knowledge base entries. No external storage required —
we store the content as text directly in the documents table.

Documents included:
  - GST return filing guidelines
  - TDS compliance rules
  - PF/ESI contribution rules
  - Income Tax filing deadlines
  - CA practice standards
  - MCA annual return requirements
"""

from __future__ import annotations

import uuid

import structlog

logger = structlog.get_logger()

# Real CA/GST/compliance content — curated from public CBIC/CBDT guidelines
_KNOWLEDGE_DOCUMENTS = [
    {
        "title": "GST Return Filing — GSTR-1, GSTR-3B, GSTR-9 Guidelines",
        "content": """GST Return Filing Guidelines (2026-27)

GSTR-1 — Outward Supplies
- Due: 11th of the following month (monthly filers)
- Due: 13th after quarter end (QRMP scheme)
- Contains: Invoice-wise details of all outward supplies
- HSN summary mandatory for turnover > 5 Cr

GSTR-3B — Summary Return
- Due: 20th of the following month
- Contains: Summary of outward/inward supplies, ITC claimed, tax paid
- Late fee: Rs 50/day (Rs 20/day for nil return), max Rs 10,000

GSTR-9 — Annual Return
- Due: 31st December of following financial year
- Mandatory for turnover > 2 Cr
- Contains: Consolidated details of GSTR-1 and GSTR-3B

GSTR-9C — Reconciliation Statement
- Required if turnover > 5 Cr
- Must be certified by a Chartered Accountant
- Reconciles GSTR-9 with audited financial statements

ITC Rules:
- ITC can be claimed only if supplier has filed their GSTR-1
- 2A/2B matching is mandatory
- ITC reversal required for non-payment within 180 days
- Rule 36(4): ITC limited to 105% of ITC in GSTR-2B""",
        "category": "gst",
    },
    {
        "title": "TDS Compliance — Sections, Rates, and Due Dates",
        "content": """TDS Compliance Guide (FY 2026-27)

Key TDS Sections:
- 194A: Interest (other than on securities) — 10%
- 194C: Contractor payments — 1% (individual) / 2% (others)
- 194H: Commission/brokerage — 5%
- 194I: Rent — 2% (P&M) / 10% (land/building)
- 194J: Professional/technical fees — 2% (technical) / 10% (professional)
- 194Q: Purchase of goods > 50L — 0.1%
- 194R: Perquisites/benefits — 10%

TDS Return Filing:
- Form 24Q: Salary TDS (quarterly)
- Form 26Q: Non-salary TDS (quarterly)
- Form 27Q: TDS on payments to NRIs
- Due dates: Q1 (31 Jul), Q2 (31 Oct), Q3 (31 Jan), Q4 (31 May)

TDS Deposit:
- By 7th of following month
- March: by 30th April
- Government deductors: same day

Lower/Nil Deduction Certificate:
- Apply via Form 13 on TRACES
- Valid for one financial year
- Must verify before accepting""",
        "category": "tds",
    },
    {
        "title": "PF & ESI Contribution Rules",
        "content": """Provident Fund (PF) & ESI Guidelines

PF Contribution (EPF):
- Employee: 12% of basic + DA
- Employer: 12% (3.67% EPF + 8.33% EPS)
- Admin charges: 0.50% of basic
- EDLI: 0.50% of basic
- Wage ceiling: Rs 15,000/month for EPS
- Due date: 15th of following month
- ECR filing: Online on EPFO portal

ESI Contribution:
- Employee: 0.75% of gross wages
- Employer: 3.25% of gross wages
- Wage ceiling: Rs 21,000/month
- Applicable: Establishments with 10+ employees
- Due date: 15th of following month
- Return: Half-yearly (Form 5)

Key Compliance:
- PF registration mandatory for 20+ employees
- International workers covered under SSA agreements
- Voluntary PF for establishments with <20 employees
- KYC (Aadhaar, PAN, bank) mandatory for all members""",
        "category": "payroll",
    },
    {
        "title": "Income Tax Filing — Due Dates and Audit Requirements",
        "content": """Income Tax Compliance Calendar (AY 2027-28)

Filing Due Dates:
- Non-audit cases: 31st July 2027
- Audit cases (44AB): 31st October 2027
- Transfer pricing (92E): 30th November 2027
- Revised return: 31st December 2027
- Belated return: 31st December 2027

Tax Audit (Section 44AB):
- Turnover > 10 Cr (if cash receipts < 5%): Audit required
- Turnover > 1 Cr (all others): Audit required
- Professional receipts > 50 Lakh: Audit required
- Form 3CA/3CB + Form 3CD
- Must be signed by a practicing CA

Advance Tax:
- 15% by 15th June
- 45% by 15th September
- 75% by 15th December
- 100% by 15th March
- Interest u/s 234B/234C for shortfall

Key Forms:
- ITR-1 (Sahaj): Salary + one house property + other sources < 50L
- ITR-3: Business/profession income
- ITR-4 (Sugam): Presumptive taxation
- ITR-5: Firms, LLPs
- ITR-6: Companies (other than 11 exempt)""",
        "category": "income_tax",
    },
    {
        "title": "MCA Annual Return Filing — ROC Compliance",
        "content": """MCA Annual Return & ROC Filing Guide

Annual Forms:
- MGT-7/MGT-7A: Annual Return
  Due: Within 60 days of AGM
  Penalty: Rs 100/day, max Rs 5,00,000

- AOC-4/AOC-4 CFS: Financial Statements
  Due: Within 30 days of AGM
  Penalty: Rs 100/day, max Rs 5,00,000

- ADT-1: Appointment of Auditor
  Due: Within 15 days of AGM

AGM Requirements:
- Must be held within 6 months of FY end
- Maximum gap: 15 months between two AGMs
- Extension: Apply via Form GNL-1 (max 3 months)

Board Meeting Requirements:
- Minimum 4 per year
- Maximum gap: 120 days
- Quorum: 1/3 of total or 2, whichever is higher

DIN/DSC:
- DIN: Apply via DIR-3 (mandatory for all directors)
- KYC: Annual DIR-3 KYC (due: 30th September)
- DSC: Required for all e-filings on MCA portal""",
        "category": "roc",
    },
    {
        "title": "CA Practice Standards — Quality Control and Ethics",
        "content": """Standards for CA Practice (ICAI Guidelines)

Standard on Quality Control (SQC) 1:
- Mandatory for all CA firms
- Leadership responsibilities for quality
- Ethical requirements (independence, integrity)
- Client acceptance and continuance policies
- Human resources (competence, assignments)
- Engagement performance standards
- Monitoring quality control system

Code of Ethics:
- Independence: No financial interest in audit clients
- Confidentiality: Client information is privileged
- Professional competence: CPE hours mandatory (40/year)
- Fee quotation: Cannot undercut below minimum
- Advertising: Limited to professional announcements

Peer Review:
- Mandatory for firms auditing entities with paid-up capital > 25 Cr
- Review cycle: Every 3 years
- Reviewer: Another practicing CA firm
- Report: Grade A (no deficiency), Grade B (minor), Grade C (major)

Practice Areas:
- Statutory audit (Companies Act 2013)
- Tax audit (Section 44AB)
- GST audit (Section 65)
- Internal audit (non-statutory)
- Management consultancy
- Forensic accounting""",
        "category": "practice",
    },
]


async def seed_knowledge_base(tenant_id_str: str) -> dict:
    """Populate the knowledge base with CA/GST compliance documents.

    Stores content as text in knowledge_documents along with a 384-dim
    BGE embedding so /knowledge/search has something real to query even
    when RAGFlow is disabled.
    """
    from sqlalchemy import text

    from core.database import engine, get_tenant_session

    tid = uuid.UUID(tenant_id_str)

    # Ensure the knowledge_documents table exists with the right schema.
    # The embedding column is added by migration v486_knowledge_embedding;
    # mirror it here so legacy-SQL-only bootstraps still work.
    async with engine.begin() as conn:
        await conn.execute(text("""
            CREATE TABLE IF NOT EXISTS knowledge_documents (
                id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                tenant_id UUID NOT NULL,
                title VARCHAR(500) NOT NULL,
                content TEXT NOT NULL,
                category VARCHAR(100),
                source VARCHAR(500),
                file_type VARCHAR(50) DEFAULT 'text',
                status VARCHAR(20) DEFAULT 'ready',
                token_count INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """))
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.execute(text(
            "ALTER TABLE knowledge_documents "
            "ADD COLUMN IF NOT EXISTS embedding vector(384)"
        ))

    # Compute embeddings for the curated corpus once, up front.
    # title + short lead of the content gives a semantically strong vector
    # that still fits in the BGE 512-token window.
    try:
        from core.embeddings import embed as _embed

        payloads = [
            f"{d['title']}\n\n{d['content'][:1500]}" for d in _KNOWLEDGE_DOCUMENTS
        ]
        vectors: list[list[float]] | None = _embed(payloads)
    except Exception as exc:
        logger.warning("seed_knowledge_embedding_skipped", error=str(exc))
        vectors = None

    created = 0
    async with get_tenant_session(tid) as session:
        for idx, doc in enumerate(_KNOWLEDGE_DOCUMENTS):
            # Check if already exists
            r = await session.execute(
                text(
                    "SELECT 1 FROM knowledge_documents "
                    "WHERE tenant_id = :tid AND title = :title LIMIT 1"
                ),
                {"tid": str(tid), "title": doc["title"]},
            )
            if r.scalar_one_or_none():
                continue

            content = doc["content"]
            token_estimate = len(content.split()) * 2  # rough token count
            vector_literal = (
                "[" + ",".join(f"{x:.6f}" for x in vectors[idx]) + "]"
                if vectors is not None
                else None
            )

            await session.execute(
                text(
                    "INSERT INTO knowledge_documents "
                    "(id, tenant_id, title, content, category, source, "
                    "file_type, status, token_count, embedding, created_at) "
                    "VALUES (:id, :tid, :title, :content, :cat, :src, "
                    "'text', 'ready', :tokens, CAST(:emb AS vector), now())"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "tid": str(tid),
                    "title": doc["title"],
                    "content": content,
                    "cat": doc.get("category", "general"),
                    "src": "CBIC/CBDT/ICAI public guidelines",
                    "tokens": token_estimate,
                    "emb": vector_literal,
                },
            )
            created += 1

    logger.info("seed_knowledge_base", tenant_id=tenant_id_str, documents=created)
    return {"documents_created": created, "total_available": len(_KNOWLEDGE_DOCUMENTS)}

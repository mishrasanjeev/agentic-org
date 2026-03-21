# Agent Workflow Visualizations

Step-by-step visual flows for key agents across all 5 departments. Each diagram shows exactly how the agent processes a real-world task — from trigger to completion.

---

## Finance

### AP Processor — Invoice Processing

When an invoice arrives via email, the AP Processor handles everything from OCR extraction to payment posting.

```mermaid
flowchart TD
    Start(("Invoice<br/>arrives via email")) --> Extract["STEP 1: EXTRACT<br/>OCR scan the PDF<br/><i>invoice_id, vendor, GSTIN,<br/>line items, total, bank details</i>"]

    Extract --> MissingCheck{"All required<br/>fields found?"}
    MissingCheck -->|"No"| Incomplete["Status: INCOMPLETE<br/>Notify AP team"]
    MissingCheck -->|"Yes"| Validate

    Validate["STEP 2: VALIDATE<br/>Check GSTIN with govt portal<br/>Check for duplicate invoice"] --> GSTINCheck{"GSTIN valid?"}

    GSTINCheck -->|"Invalid"| GSTINFail["Status: GSTIN_INVALID<br/>Notify vendor + AP team"]
    GSTINCheck -->|"Valid"| DupCheck{"Duplicate<br/>invoice?"}
    DupCheck -->|"Yes"| DupFail["Status: DUPLICATE<br/>Reference original, stop"]
    DupCheck -->|"No"| Match

    Match["STEP 3: MATCH<br/>3-way match:<br/>Invoice vs PO vs GRN"] --> MatchResult{"Invoice matches<br/>PO within tolerance?"}

    MatchResult -->|"Yes"| Schedule
    MatchResult -->|"No — mismatch"| MismatchHITL

    MismatchHITL["HITL GATE<br/>CFO reviews mismatch<br/><i>Shows: delta amount, vendor details,<br/>recommendation, full trace</i>"]

    MismatchHITL -->|"CFO approves"| Schedule
    MismatchHITL -->|"CFO rejects"| Rejected["Invoice Rejected<br/>Vendor notified"]

    Schedule["STEP 4: SCHEDULE<br/>Queue payment with<br/>early-payment discount"] --> AmountCheck{"Amount > ₹5L?"}

    AmountCheck -->|"Yes"| PaymentHITL["HITL GATE<br/>CFO approves payment"]
    AmountCheck -->|"No"| Post
    PaymentHITL -->|"Approved"| Post

    Post["STEP 5: POST<br/>GL journal entry posted<br/><i>idempotency_key prevents duplicates</i>"] --> Notify

    Notify["STEP 6: NOTIFY<br/>Remittance advice<br/>sent to vendor"] --> Done(("Done"))

    style Start fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style Extract fill:#f1f8e9,stroke:#33691e
    style Validate fill:#f1f8e9,stroke:#33691e
    style Match fill:#f1f8e9,stroke:#33691e
    style Schedule fill:#f1f8e9,stroke:#33691e
    style Post fill:#f1f8e9,stroke:#33691e
    style Notify fill:#f1f8e9,stroke:#33691e
    style MismatchHITL fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style PaymentHITL fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style Incomplete fill:#fff3e0,stroke:#e65100
    style GSTINFail fill:#fff3e0,stroke:#e65100
    style DupFail fill:#fff3e0,stroke:#e65100
    style Rejected fill:#fff3e0,stroke:#e65100
    style Done fill:#e0f2f1,stroke:#00695c,stroke-width:2px
```

### Reconciliation Agent — Daily Bank Recon

Every day at T+0, the Recon Agent matches every bank transaction to a GL entry automatically.

```mermaid
flowchart TD
    Start(("Daily trigger<br/>T+0")) --> Fetch["STEP 1: FETCH<br/>Pull today's bank transactions<br/>Pull GL entries for same date"]

    Fetch --> MatchLoop["STEP 2: MATCH<br/>For each bank transaction:<br/>Find GL entry by amount + date + ref"]

    MatchLoop --> Result{"Match<br/>found?"}

    Result -->|"Exact match"| AutoPost["Auto-post reconciliation<br/><i>No human needed</i>"]
    Result -->|"No match"| Suggest["Generate ranked<br/>GL suggestions"]

    Suggest --> BreakSize{"Break amount<br/>> ₹50K?"}

    BreakSize -->|"Yes"| HighBreak["HITL GATE<br/>Finance Manager reviews<br/><i>Slack + email alert</i>"]
    BreakSize -->|"No"| LowBreak["Auto-suggest top match<br/>Flag for review"]

    AutoPost --> Report
    HighBreak --> Report
    LowBreak --> Report

    Report["STEP 3: REPORT<br/>Recon report generated<br/><i>Matched / Unmatched / Breaks</i>"] --> Done(("Done<br/>99.7% accuracy"))

    style Start fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style Fetch fill:#f1f8e9,stroke:#33691e
    style MatchLoop fill:#f1f8e9,stroke:#33691e
    style AutoPost fill:#e0f2f1,stroke:#00695c
    style HighBreak fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style Report fill:#f1f8e9,stroke:#33691e
    style Done fill:#e0f2f1,stroke:#00695c,stroke-width:2px
```

---

## Human Resources

### Talent Acquisition — Hiring Pipeline

From job description to offer letter — the full hiring flow automated with bias-free screening.

```mermaid
flowchart TD
    Start(("Hiring<br/>request")) --> JD["STEP 1: GENERATE JD<br/>Create structured JD<br/><i>Inclusive language<br/>Market comp benchmarks</i>"]

    JD --> Post["STEP 2: POST<br/>Publish to LinkedIn,<br/>Naukri, Indeed via Greenhouse"]

    Post --> Screen["STEP 3: SCREEN<br/>Strip all PII before scoring<br/><i>No name, gender, age, photo</i><br/>Score against structured rubric"]

    Screen --> BiasCheck["Bias check:<br/>Gender distribution in shortlist<br/>matches applicant pool"]

    BiasCheck --> Rank["Rank candidates<br/>by rubric score only"]

    Rank --> Schedule["STEP 4: SCHEDULE<br/>Find common slot for 5 panelists<br/>Book Zoom, send invites"]

    Schedule --> Evaluate["STEP 5: EVALUATE<br/>Aggregate panel feedback<br/>Compute composite score"]

    Evaluate --> Offer["STEP 6: OFFER<br/>Generate offer letter<br/><i>Band-validated comp</i>"]

    Offer --> HITL["HITL GATE<br/>HR Head reviews offer<br/>before DocuSign send"]

    HITL -->|"Approved"| Send["Offer sent via DocuSign<br/>Candidate notified"]
    HITL -->|"Rejected"| Revise["Revise compensation<br/>or reject candidate"]

    Send --> Accept{"Candidate<br/>accepts?"}
    Accept -->|"Yes"| Onboard(("Trigger<br/>Onboarding Agent"))
    Accept -->|"No"| NextCandidate["Move to next<br/>ranked candidate"]

    style Start fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style JD fill:#f1f8e9,stroke:#33691e
    style Post fill:#f1f8e9,stroke:#33691e
    style Screen fill:#f1f8e9,stroke:#33691e
    style BiasCheck fill:#fff3e0,stroke:#e65100
    style Schedule fill:#f1f8e9,stroke:#33691e
    style Evaluate fill:#f1f8e9,stroke:#33691e
    style Offer fill:#f1f8e9,stroke:#33691e
    style HITL fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style Send fill:#e0f2f1,stroke:#00695c
    style Onboard fill:#e0f2f1,stroke:#00695c,stroke-width:2px
```

### Payroll Engine — Monthly Payroll Run

The most critical HR agent — computes salary for every employee with zero tolerance for error.

```mermaid
flowchart TD
    Start(("Monthly<br/>payroll trigger")) --> Gather["STEP 1: GATHER<br/>Fetch attendance data<br/>Fetch leave balances<br/>Fetch variable pay inputs"]

    Gather --> DataCheck{"All attendance<br/>data present?"}
    DataCheck -->|"No — missing data"| Error["ERROR: STOP<br/>Never assume or extrapolate<br/><i>Missing data = error, not default</i>"]
    DataCheck -->|"Yes"| Compute

    Compute["STEP 2: COMPUTE<br/>For each employee:"] --> Gross["Calculate Gross Salary<br/><i>Base + HRA + DA + variable</i>"]

    Gross --> Deductions["Apply Deductions<br/><i>PF (12%) + ESI + PT + TDS + LWF</i>"]

    Deductions --> Net["Net Pay = Gross - Deductions<br/><i>Cross-check: must balance</i>"]

    Net --> Validate["STEP 3: VALIDATE<br/>Verify totals ± ₹1<br/>Flag any discrepancy"]

    Validate --> HITL["HITL GATE (ALWAYS)<br/>HR Head + Finance sign-off<br/><i>Every payroll run requires<br/>human approval — no exceptions</i>"]

    HITL -->|"Approved"| Disburse["STEP 5: DISBURSE<br/>Queue salary payments<br/>Generate payslips"]

    Disburse --> File["STEP 6: FILE<br/>EPFO ECR + ESIC returns<br/>TDS via 24Q"]

    File --> FileHITL["HITL GATE<br/>HR Head approves<br/>every statutory filing"]

    FileHITL -->|"Approved"| Submit["Filings submitted<br/>to govt portals"] --> Done(("Done"))

    style Start fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style Gather fill:#f1f8e9,stroke:#33691e
    style Error fill:#fce4ec,stroke:#c62828
    style Compute fill:#f1f8e9,stroke:#33691e
    style Gross fill:#fff3e0,stroke:#e65100
    style Deductions fill:#fff3e0,stroke:#e65100
    style Net fill:#fff3e0,stroke:#e65100
    style Validate fill:#f1f8e9,stroke:#33691e
    style HITL fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style Disburse fill:#f1f8e9,stroke:#33691e
    style File fill:#f1f8e9,stroke:#33691e
    style FileHITL fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style Done fill:#e0f2f1,stroke:#00695c,stroke-width:2px
```

### Onboarding Agent — Day-0 Provisioning

New employee starts today. Every system provisioned automatically before they walk in.

```mermaid
flowchart TD
    Start(("New hire record<br/>created in Darwinbox")) --> Provision["STEP 1: PROVISION<br/>Day-0 system access"]

    Provision --> Systems["Create accounts in parallel:"]

    Systems --> Okta["Okta SSO<br/><i>email + groups</i>"]
    Systems --> Slack2["Slack<br/><i>add to channels</i>"]
    Systems --> Jira2["Jira<br/><i>project access</i>"]
    Systems --> GitHub2["GitHub<br/><i>org + repos</i>"]
    Systems --> Confluence2["Confluence<br/><i>spaces</i>"]

    Okta --> ProdCheck{"Production<br/>access requested?"}
    Slack2 --> Equipment
    Jira2 --> Equipment
    GitHub2 --> ProdCheck
    Confluence2 --> Equipment

    ProdCheck -->|"Yes"| ProdHITL["HITL GATE<br/>Manager approves<br/>production access"]
    ProdCheck -->|"No"| Equipment

    ProdHITL --> Equipment
    Equipment["STEP 2: EQUIPMENT<br/>Create Jira ticket for<br/>laptop + peripherals"]

    Equipment --> Training["STEP 3: TRAINING<br/>Enroll in compliance training<br/>Schedule orientation meetings"]

    Training --> Plan["STEP 4: PLAN<br/>Create 30/60/90 day plan<br/>in Confluence<br/>Assign buddy"]

    Plan --> Verify["STEP 5: VERIFY<br/>Confirm all systems accessible<br/>Send welcome message on Slack"]

    Verify --> Done(("Employee ready<br/>on Day 0"))

    style Start fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style Provision fill:#f1f8e9,stroke:#33691e
    style Okta fill:#e8eaf6,stroke:#283593
    style Slack2 fill:#e8eaf6,stroke:#283593
    style Jira2 fill:#e8eaf6,stroke:#283593
    style GitHub2 fill:#e8eaf6,stroke:#283593
    style Confluence2 fill:#e8eaf6,stroke:#283593
    style ProdHITL fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style Equipment fill:#f1f8e9,stroke:#33691e
    style Training fill:#f1f8e9,stroke:#33691e
    style Plan fill:#f1f8e9,stroke:#33691e
    style Verify fill:#f1f8e9,stroke:#33691e
    style Done fill:#e0f2f1,stroke:#00695c,stroke-width:2px
```

---

## Marketing

### Campaign Pilot — Budget Optimization

Monitors ad campaigns across Google, Meta, and LinkedIn — automatically reallocates spend to top performers.

```mermaid
flowchart TD
    Start(("Hourly<br/>performance check")) --> Monitor["STEP 1: MONITOR<br/>Pull performance from all channels"]

    Monitor --> Channels["Fetch metrics:"]
    Channels --> Google["Google Ads<br/><i>ROAS, CPA, CTR</i>"]
    Channels --> Meta["Meta Ads<br/><i>ROAS, CPA, CTR</i>"]
    Channels --> LinkedIn["LinkedIn Ads<br/><i>ROAS, CPA, CTR</i>"]

    Google --> Analyze
    Meta --> Analyze
    LinkedIn --> Analyze

    Analyze["STEP 2: OPTIMIZE<br/>Identify underperformers<br/>Compute reallocation plan<br/><i>Within approved budget cap</i>"] --> ShiftSize{"Budget shift<br/>> ₹50K?"}

    ShiftSize -->|"≤ ₹50K"| AutoExecute["Auto-execute reallocation<br/><i>Pause underperformers<br/>Boost top performers</i>"]
    ShiftSize -->|"> ₹50K"| HITL["HITL GATE<br/>CMO approves<br/>budget reallocation"]

    HITL -->|"Approved"| Execute["Execute reallocation"]
    HITL -->|"Rejected"| NoChange["Keep current allocation"]

    AutoExecute --> Report
    Execute --> Report

    Report["STEP 4: REPORT<br/>Update A/B test results<br/>Track winner selection<br/>Update attribution model"] --> Done(("Optimization<br/>complete"))

    style Start fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style Monitor fill:#f1f8e9,stroke:#33691e
    style Google fill:#e8eaf6,stroke:#283593
    style Meta fill:#e8eaf6,stroke:#283593
    style LinkedIn fill:#e8eaf6,stroke:#283593
    style Analyze fill:#f1f8e9,stroke:#33691e
    style AutoExecute fill:#e0f2f1,stroke:#00695c
    style HITL fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style Report fill:#f1f8e9,stroke:#33691e
    style Done fill:#e0f2f1,stroke:#00695c,stroke-width:2px
```

### Brand Monitor — Crisis Detection

Scans 50+ channels for brand mentions. When a crisis signal is detected, the PR team is alerted immediately.

```mermaid
flowchart TD
    Start(("Continuous<br/>monitoring")) --> Scan["STEP 1: SCAN<br/>Monitor brand mentions<br/>across 50+ channels<br/><i>Twitter, news, forums, reviews</i>"]

    Scan --> Analyze["STEP 2: ANALYZE<br/>Compute sentiment trends<br/>Detect volume spikes<br/>Track share of voice"]

    Analyze --> CrisisCheck{"Crisis signal<br/>detected?"}

    CrisisCheck -->|"No"| Weekly["Generate weekly<br/>intelligence brief"]
    CrisisCheck -->|"Yes — viral negative<br/>or media pickup"| Crisis

    Crisis["CRISIS ALERT<br/>Immediate HITL escalation<br/>to PR team"] --> PRTeam["PR team receives:"]

    PRTeam --> Context["Full context:<br/>• Mention volume spike<br/>• Sentiment trajectory<br/>• Source channels<br/>• Sample mentions"]

    PRTeam --> Drafts["Draft response options<br/><i>Agent NEVER posts publicly<br/>All responses via PR team</i>"]

    Context --> Response["PR team decides<br/>public response"]
    Drafts --> Response

    Response --> Track["Track resolution<br/>Monitor sentiment recovery"]

    Weekly --> Done(("Report<br/>delivered"))
    Track --> Done

    style Start fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style Scan fill:#f1f8e9,stroke:#33691e
    style Analyze fill:#f1f8e9,stroke:#33691e
    style Crisis fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style Weekly fill:#e0f2f1,stroke:#00695c
    style Done fill:#e0f2f1,stroke:#00695c,stroke-width:2px
```

---

## Operations

### Support Triage — Ticket Resolution

Customer submits a ticket. L1 resolved automatically; L2+ enriched with full context and routed to the right team.

```mermaid
flowchart TD
    Start(("New Zendesk<br/>ticket created")) --> Classify["STEP 1: CLASSIFY<br/>Analyze subject + body<br/>Check customer history<br/>Compute sentiment score"]

    Classify --> Severity{"Ticket<br/>classification?"}

    Severity -->|"L1 — Known issue"| L1["STEP 2: RESOLVE L1<br/>Apply resolution macro<br/>Respond to customer<br/><i>Target: 65% L1 containment</i>"]

    L1 --> Resolved{"Customer<br/>satisfied?"}
    Resolved -->|"Yes"| Close["Close ticket<br/>CSAT survey sent"]
    Resolved -->|"No"| Escalate

    Severity -->|"L2+ — Complex"| Enrich["STEP 3: ENRICH<br/>Gather full context:<br/>• Customer tier (VIP?)<br/>• Purchase history<br/>• Prior ticket history<br/>• Sentiment analysis"]

    Enrich --> VIPCheck{"VIP customer<br/>OR sentiment < -0.6?"}

    VIPCheck -->|"Yes"| Escalate["HITL GATE<br/>Route to senior support<br/>with full context"]
    VIPCheck -->|"No"| Route["Route to<br/>appropriate team"]

    Escalate --> Assign["Human agent<br/>takes over with<br/>full enriched context"]

    Route --> Assign
    Close --> Done(("Ticket<br/>resolved"))
    Assign --> Done

    style Start fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style Classify fill:#f1f8e9,stroke:#33691e
    style L1 fill:#f1f8e9,stroke:#33691e
    style Close fill:#e0f2f1,stroke:#00695c
    style Enrich fill:#f1f8e9,stroke:#33691e
    style Escalate fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style Done fill:#e0f2f1,stroke:#00695c,stroke-width:2px
```

### Vendor Manager — Onboarding & KYC

New vendor? The agent runs sanctions screening, GSTIN validation, and risk scoring before creating the ERP record.

```mermaid
flowchart TD
    Start(("Vendor onboarding<br/>request")) --> KYC["STEP 1: ONBOARD<br/>KYC verification"]

    KYC --> Parallel["Run checks in parallel:"]

    Parallel --> Sanctions["Sanctions Screening<br/><i>OFAC / UN / EU lists</i>"]
    Parallel --> GSTIN["GSTIN Validation<br/><i>Govt portal check</i>"]
    Parallel --> MCA["MCA Company Data<br/><i>Director details, status</i>"]

    Sanctions --> SanctionsResult{"Sanctions<br/>hit?"}
    SanctionsResult -->|"Yes — ANY hit"| Blocked["BLOCKED<br/>Compliance alerted<br/>No ERP record created<br/><i>Never dismiss without<br/>human review</i>"]

    SanctionsResult -->|"Clean"| RiskScore
    GSTIN --> RiskScore
    MCA --> RiskScore

    RiskScore["STEP 2: ASSESS<br/>Compute vendor risk score<br/><i>0-10 scale</i>"] --> RiskCheck{"Risk score<br/>> 7?"}

    RiskCheck -->|"Yes"| RiskHITL["HITL GATE<br/>Procurement Manager<br/>reviews high-risk vendor"]
    RiskCheck -->|"No"| CreateVendor

    RiskHITL -->|"Approved"| CreateVendor
    RiskHITL -->|"Rejected"| VendorRejected["Vendor Rejected<br/>Notified with reason"]

    CreateVendor["STEP 3: CREATE<br/>Vendor record in Oracle Fusion<br/>Welcome email sent"]

    CreateVendor --> Monitor["STEP 4: MONITOR<br/>SLA compliance tracking<br/>Performance scorecard<br/>Contract renewal alerts"]

    Monitor --> Done(("Vendor<br/>onboarded"))

    style Start fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style KYC fill:#f1f8e9,stroke:#33691e
    style Sanctions fill:#e8eaf6,stroke:#283593
    style GSTIN fill:#e8eaf6,stroke:#283593
    style MCA fill:#e8eaf6,stroke:#283593
    style Blocked fill:#fce4ec,stroke:#c62828,stroke-width:3px
    style RiskScore fill:#f1f8e9,stroke:#33691e
    style RiskHITL fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style CreateVendor fill:#f1f8e9,stroke:#33691e
    style Monitor fill:#f1f8e9,stroke:#33691e
    style Done fill:#e0f2f1,stroke:#00695c,stroke-width:2px
```

---

## Back Office

### Risk Sentinel — Fraud Detection & SAR

Continuously monitors financial transactions for suspicious patterns. Any hit triggers immediate human review.

```mermaid
flowchart TD
    Start(("Continuous<br/>transaction feed")) --> Monitor["STEP 1: MONITOR<br/>Scan all financial transactions"]

    Monitor --> Patterns["Check for fraud patterns:"]

    Patterns --> P1["Unusual amounts<br/><i>Outside normal range</i>"]
    Patterns --> P2["Unusual frequency<br/><i>Spike in transactions</i>"]
    Patterns --> P3["New beneficiaries<br/><i>Unknown recipients</i>"]
    Patterns --> P4["Round-trip payments<br/><i>Circular fund flow</i>"]

    P1 --> AnomalyCheck
    P2 --> AnomalyCheck
    P3 --> AnomalyCheck
    P4 --> AnomalyCheck

    AnomalyCheck{"Suspicious<br/>pattern<br/>detected?"} -->|"No"| Continue["Continue monitoring"]

    AnomalyCheck -->|"Yes"| Screen["STEP 2: SCREEN<br/>Run sanctions/PEP screening<br/>on all parties involved"]

    Screen --> SanctionsHit{"Sanctions<br/>match?"}

    SanctionsHit -->|"Yes"| SanctionsAlert["IMMEDIATE HITL<br/>Compliance Officer notified<br/><i>Never dismiss a sanctions hit<br/>without human review</i>"]

    SanctionsHit -->|"No — but suspicious"| SAR

    SAR["STEP 3: DETECT<br/>Generate SAR draft<br/><i>Suspicious Activity Report</i><br/>with full evidence package"]

    SAR --> HITL["HITL GATE<br/>MLRO reviews SAR<br/><i>Money Laundering<br/>Reporting Officer</i>"]

    HITL -->|"File SAR"| File["SAR filed with<br/>regulatory authority"]
    HITL -->|"Dismiss"| Dismiss["Marked as reviewed<br/>Decision logged in audit"]
    SanctionsAlert --> HITL

    File --> Done(("Done"))
    Dismiss --> Done
    Continue --> Done

    style Start fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style Monitor fill:#f1f8e9,stroke:#33691e
    style P1 fill:#fff3e0,stroke:#e65100
    style P2 fill:#fff3e0,stroke:#e65100
    style P3 fill:#fff3e0,stroke:#e65100
    style P4 fill:#fff3e0,stroke:#e65100
    style SanctionsAlert fill:#fce4ec,stroke:#c62828,stroke-width:3px
    style SAR fill:#f1f8e9,stroke:#33691e
    style HITL fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style Done fill:#e0f2f1,stroke:#00695c,stroke-width:2px
```

### Compliance Guard — Regulatory Filing

Tracks every regulatory deadline. Prepares filing packages automatically. Never auto-files — always requires human sign-off.

```mermaid
flowchart TD
    Start(("Regulatory<br/>calendar check")) --> Calendar["STEP 1: CALENDAR<br/>Track all filing deadlines<br/><i>GSTN, MCA, SEBI, RBI,<br/>Income Tax, EPFO, ESIC</i>"]

    Calendar --> Upcoming{"Filing due<br/>within 7 days?"}

    Upcoming -->|"No"| Monitor["Continue monitoring<br/><i>Daily check</i>"]
    Upcoming -->|"Yes"| Prepare

    Prepare["STEP 2: PREPARE<br/>Auto-generate filing package<br/><i>D-7 before deadline</i>"] --> GatherData["Gather required data<br/>from ERP + portals"]

    GatherData --> Validate["STEP 3: REVIEW<br/>Run compliance checks<br/>Validate completeness"]

    Validate --> AmbiguousCheck{"Ambiguous regulatory<br/>text found?"}

    AmbiguousCheck -->|"Yes"| LegalReview["Flag for qualified<br/>legal/compliance review<br/><i>Agent never interprets<br/>ambiguous regulations</i>"]
    AmbiguousCheck -->|"No"| Ready

    LegalReview --> Ready

    Ready["Filing package ready<br/>All data validated"] --> HITL["HITL GATE (ALWAYS)<br/>Compliance Officer sign-off<br/><i>ALL regulatory filings require<br/>human approval — no exceptions</i>"]

    HITL -->|"Approved"| Submit["STEP 4: SUBMIT<br/>File with regulatory portal"]
    HITL -->|"Needs revision"| Prepare

    Submit --> Confirm["Confirmation received<br/>Filing status tracked"] --> Done(("Filing<br/>complete"))

    style Start fill:#e1f5fe,stroke:#01579b,stroke-width:2px
    style Calendar fill:#f1f8e9,stroke:#33691e
    style Prepare fill:#f1f8e9,stroke:#33691e
    style Validate fill:#f1f8e9,stroke:#33691e
    style HITL fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style Submit fill:#f1f8e9,stroke:#33691e
    style Done fill:#e0f2f1,stroke:#00695c,stroke-width:2px
```

---

## Visual Legend

```mermaid
graph LR
    A["Processing Step"] --> B{"Decision<br/>Point"}
    B --> C["HITL Gate<br/>Human Required"]
    B --> D(("Completed"))
    B --> E["Error / Blocked"]

    style A fill:#f1f8e9,stroke:#33691e
    style B fill:#fff3e0,stroke:#e65100
    style C fill:#fce4ec,stroke:#c62828,stroke-width:2px
    style D fill:#e0f2f1,stroke:#00695c,stroke-width:2px
    style E fill:#fff3e0,stroke:#e65100
```

| Color | Meaning |
|-------|---------|
| Green | Processing step (automated) |
| Yellow/Orange | Decision point or error state |
| Red | HITL gate — requires human approval |
| Blue | Trigger / input / parallel tasks |
| Teal | Successfully completed |

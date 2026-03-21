# Agent Guide

## Overview

AgentFlow OS ships with 24 specialist agents across 5 enterprise domains, coordinated by the NEXUS orchestrator. Each agent has:

- **Production system prompt** with domain-specific instructions
- **Token scope** limiting which connectors and actions it can use
- **Processing sequence** defining step-by-step execution order
- **Confidence floor** triggering HITL escalation when reasoning quality drops
- **Anti-hallucination rules** preventing fabricated data

## Agent Domain Hierarchy

```mermaid
graph LR
    NEXUS((NEXUS<br/>Orchestrator))

    subgraph Finance["Finance (6 agents)"]
        AP["AP Processor<br/><i>88%</i>"]
        AR["AR Collections<br/><i>85%</i>"]
        Recon["Reconciliation<br/><i>95%</i>"]
        Tax["Tax Compliance<br/><i>92%</i>"]
        Close["Close Agent<br/><i>80%</i>"]
        FPA["FP&A Agent<br/><i>78%</i>"]
    end

    subgraph HR["HR (6 agents)"]
        TA["Talent Acquisition<br/><i>88%</i>"]
        Onboard["Onboarding<br/><i>95%</i>"]
        Payroll["Payroll Engine<br/><i>99%</i>"]
        Perf["Performance Coach<br/><i>80%</i>"]
        LD["L&D Coordinator<br/><i>82%</i>"]
        Offboard["Offboarding<br/><i>95%</i>"]
    end

    subgraph Marketing["Marketing (5 agents)"]
        Content["Content Factory<br/><i>88%</i>"]
        Campaign["Campaign Pilot<br/><i>85%</i>"]
        SEO["SEO Strategist<br/><i>90%</i>"]
        CRM["CRM Intelligence<br/><i>88%</i>"]
        Brand["Brand Monitor<br/><i>85%</i>"]
    end

    subgraph Operations["Operations (5 agents)"]
        Vendor["Vendor Manager<br/><i>88%</i>"]
        Contract["Contract Intel<br/><i>82%</i>"]
        Support["Support Triage<br/><i>85%</i>"]
        Compliance["Compliance Guard<br/><i>95%</i>"]
        ITOps["IT Operations<br/><i>88%</i>"]
    end

    subgraph BackOffice["Back Office (3 agents)"]
        Legal["Legal Ops<br/><i>90%</i>"]
        Risk["Risk Sentinel<br/><i>95%</i>"]
        Facilities["Facilities<br/><i>80%</i>"]
    end

    NEXUS --> Finance
    NEXUS --> HR
    NEXUS --> Marketing
    NEXUS --> Operations
    NEXUS --> BackOffice

    style NEXUS fill:#e1f5fe,stroke:#01579b,stroke-width:3px,color:#000
    style Finance fill:#fff3e0,stroke:#e65100,stroke-width:2px
    style HR fill:#f1f8e9,stroke:#33691e,stroke-width:2px
    style Marketing fill:#f3e5f5,stroke:#6a1b9a,stroke-width:2px
    style Operations fill:#e8eaf6,stroke:#283593,stroke-width:2px
    style BackOffice fill:#fce4ec,stroke:#c62828,stroke-width:2px
```

> Percentages shown are the confidence floor for each agent. Below this floor, the agent triggers HITL escalation.

---

## Agent Execution Flow

```mermaid
sequenceDiagram
    autonumber
    participant NEXUS as NEXUS Orchestrator
    participant Agent as Specialist Agent
    participant LLM as LLM Backbone
    participant TGW as Tool Gateway
    participant HITL as HITL Queue

    NEXUS->>Agent: Assign task + context
    Agent->>Agent: Load system prompt

    loop Reasoning Steps
        Agent->>LLM: Prompt with context + tools
        LLM-->>Agent: Structured response

        opt Tool call needed
            Agent->>TGW: Execute tool
            TGW-->>Agent: Result (PII masked)
        end

        Agent->>Agent: Update processing trace
    end

    Agent->>Agent: Compute confidence score

    alt confidence >= floor
        Agent-->>NEXUS: TaskResult (confident)
    else confidence < floor
        Agent-->>NEXUS: TaskResult (low confidence)
        NEXUS->>HITL: Escalate for human review
    end
```

---

## Agent Inventory

### Finance (6 agents)

| Agent | Type | Confidence | Key Capabilities |
|-------|------|-----------|-----------------|
| **AP Processor** | `ap_processor` | 88% | Invoice OCR, GSTIN validation, 3-way match, payment scheduling, GL posting |
| **AR Collections** | `ar_collections` | 85% | Aging analysis, tiered communications (email/WhatsApp/call), payment links |
| **Reconciliation** | `recon_agent` | 95% | Bank-to-GL matching at T+0, break detection, ranked GL suggestions |
| **Tax Compliance** | `tax_compliance` | 92% | GST/TDS computation, GSTR-1/3B/9 prep, portal reconciliation |
| **Close Agent** | `close_agent` | 80% | Month-end close checklist, P&L/BS/CF drafting, CFO sign-off gate |
| **FP&A Agent** | `fpa_agent` | 78% | Cash forecasting, budget variance, scenario modelling, board packs |

```mermaid
graph LR
    subgraph "Finance Agent Connectors"
        AP_A["AP Processor"] -->|read/write| OF["Oracle Fusion"]
        AP_A -->|read| GSTN["GSTN Portal"]
        AP_A -->|read| Bank["Banking AA"]

        AR_A["AR Collections"] -->|read| OF
        AR_A -->|write| SG["SendGrid"]
        AR_A -->|write| WA["WhatsApp"]
        AR_A -->|write| Stripe["Stripe"]

        Recon_A["Reconciliation"] -->|read| OF
        Recon_A -->|read| Bank

        Tax_A["Tax Compliance"] -->|read/write| GSTN
        Tax_A -->|read| IT["Income Tax Portal"]
    end

    style AP_A fill:#fff3e0,stroke:#e65100
    style AR_A fill:#fff3e0,stroke:#e65100
    style Recon_A fill:#fff3e0,stroke:#e65100
    style Tax_A fill:#fff3e0,stroke:#e65100
```

### HR (6 agents)

| Agent | Type | Confidence | Key Capabilities |
|-------|------|-----------|-----------------|
| **Talent Acquisition** | `talent_acquisition` | 88% | JD generation, multi-board posting, bias-free screening, interview scheduling |
| **Onboarding** | `onboarding_agent` | 95% | Day-0 system provisioning, 30/60/90 plans, compliance training |
| **Payroll Engine** | `payroll_engine` | 99% | Gross-to-net, PF/ESI/PT/TDS/LWF deductions, EPFO ECR filing |
| **Performance Coach** | `performance_coach` | 80% | OKR tracking, 360 feedback, attrition risk scoring |
| **L&D Coordinator** | `ld_coordinator` | 82% | Skill gap analysis, learning paths, training scheduling |
| **Offboarding** | `offboarding_agent` | 95% | Access revocation, F&F settlement, experience letters, data archival |

```mermaid
graph LR
    subgraph "HR Agent Connectors"
        TA_A["Talent Acquisition"] -->|read/write| GH["Greenhouse"]
        TA_A -->|write| SG2["SendGrid"]

        OB_A["Onboarding"] -->|write| Okta["Okta SCIM"]
        OB_A -->|write| DBox["Darwinbox"]
        OB_A -->|write| Slack["Slack"]

        Pay_A["Payroll Engine"] -->|read/write| DBox
        Pay_A -->|write| EPFO["EPFO Portal"]
        Pay_A -->|read| Keka["Keka HR"]

        Off_A["Offboarding"] -->|write| Okta
        Off_A -->|write| DBox
        Off_A -->|write| GH
    end

    style TA_A fill:#f1f8e9,stroke:#33691e
    style OB_A fill:#f1f8e9,stroke:#33691e
    style Pay_A fill:#f1f8e9,stroke:#33691e
    style Off_A fill:#f1f8e9,stroke:#33691e
```

### Marketing (5 agents)

| Agent | Type | Confidence | Key Capabilities |
|-------|------|-----------|-----------------|
| **Content Factory** | `content_factory` | 88% | SEO content creation, brand guidelines, NEVER auto-publishes |
| **Campaign Pilot** | `campaign_pilot` | 85% | A/B testing, spend optimization, ROAS tracking |
| **SEO Strategist** | `seo_strategist` | 90% | Keyword analysis, content gaps, technical SEO recommendations |
| **CRM Intelligence** | `crm_intelligence` | 88% | Lead scoring, nurture sequences, churn risk monitoring |
| **Brand Monitor** | `brand_monitor` | 85% | Sentiment analysis, crisis detection, share of voice |

### Operations (5 agents)

| Agent | Type | Confidence | Key Capabilities |
|-------|------|-----------|-----------------|
| **Vendor Manager** | `vendor_manager` | 88% | KYC verification, sanctions screening, PO management, SLA monitoring |
| **Contract Intelligence** | `contract_intelligence` | 82% | Metadata extraction, clause analysis, renewal monitoring |
| **Support Triage** | `support_triage` | 85% | L1 resolution, L2 enrichment, sentiment-based routing |
| **Compliance Guard** | `compliance_guard` | 95% | Regulatory calendar, filing prep, compliance reporting |
| **IT Operations** | `it_operations` | 88% | Ticket triage, access provisioning, incident runbooks |

### Back Office (3 agents)

| Agent | Type | Confidence | Key Capabilities |
|-------|------|-----------|-----------------|
| **Legal Ops** | `legal_ops` | 90% | NDA routing, contract review, board resolution drafting |
| **Risk Sentinel** | `risk_sentinel` | 95% | Fraud pattern detection, sanctions screening, SAR drafting |
| **Facilities** | `facilities_agent` | 80% | Procurement, asset tracking, maintenance scheduling |

---

## Agent Lifecycle

```mermaid
stateDiagram-v2
    [*] --> draft: create_agent()
    draft --> shadow: start_shadow

    shadow --> review_ready: accuracy >= 95%
    shadow --> shadow_failing: accuracy < floor
    shadow_failing --> shadow: retrain / adjust
    shadow_failing --> deprecated: abandon

    review_ready --> staging: reviewer_approve
    staging --> production_ready: all 6 gates pass
    production_ready --> active: final_promote

    active --> paused: kill_switch / budget_exceeded
    paused --> active: resume

    active --> deprecated: sunset
    paused --> deprecated: sunset
    deprecated --> deleted: cleanup
    deleted --> [*]
```

### Shadow Mode

During shadow mode, the agent:
1. Receives the same inputs as the reference agent
2. Produces outputs in read-only mode (no side effects)
3. Outputs are compared against the reference agent's results
4. Must achieve >= 95% accuracy over >= 100 samples
5. Results stored in `shadow_comparisons` table

```mermaid
flowchart LR
    Input["Task Input"] --> Ref["Reference Agent<br/><i>(production)</i>"]
    Input --> Shadow["Shadow Agent<br/><i>(candidate)</i>"]
    Ref --> Compare["Shadow Comparator"]
    Shadow --> Compare
    Compare --> Score{"Accuracy<br/>>= 95%?"}
    Score -->|"Yes (100+ samples)"| Promote["Promote to<br/>review_ready"]
    Score -->|No| Continue["Continue<br/>shadow mode"]

    style Ref fill:#f1f8e9,stroke:#33691e
    style Shadow fill:#fff3e0,stroke:#e65100
    style Compare fill:#e1f5fe,stroke:#01579b
    style Promote fill:#e0f2f1,stroke:#00695c
```

---

## Prompt Structure

Every agent prompt follows this structure:

```
# core/agents/prompts/{agent_type}.prompt.txt  v2.0
You are the {Agent Name} for {{org_name}}.
Domain: {domain} | Confidence floor: {N}% | Max retries: 3
Token scope: {connector}({permissions}) ...

<processing_sequence>
STEP 1 — {ACTION}
  {detailed instructions with tool calls}
STEP 2 — {ACTION}
  ...
</processing_sequence>

<escalation_rules>
Trigger HITL (never auto-proceed) if ANY condition is true:
  {agent-specific conditions}
  confidence < {floor} for any step
Include in HITL context: full trace, all computed values, trigger condition, recommendation.
</escalation_rules>

<anti_hallucination>
{agent-specific rules}
NEVER invent data not from tool responses.
NEVER proceed with stale data after a tool error — retry per policy, then escalate.
</anti_hallucination>

<output_format>
{ "status":"str", "confidence":0.0-1.0, "processing_trace":[...], "tool_calls":[...] }
</output_format>
```

```mermaid
graph TB
    subgraph "Agent Prompt Architecture"
        Identity["Identity & Scope<br/><i>name, domain, confidence floor, token scope</i>"]
        Processing["Processing Sequence<br/><i>ordered steps with tool calls</i>"]
        Escalation["Escalation Rules<br/><i>HITL trigger conditions</i>"]
        AntiHalluc["Anti-Hallucination<br/><i>data integrity constraints</i>"]
        Output["Output Format<br/><i>structured JSON schema</i>"]
    end

    Identity --> Processing
    Processing --> Escalation
    Escalation --> AntiHalluc
    AntiHalluc --> Output

    style Identity fill:#e1f5fe,stroke:#01579b
    style Processing fill:#f1f8e9,stroke:#33691e
    style Escalation fill:#fce4ec,stroke:#c62828
    style AntiHalluc fill:#fff3e0,stroke:#e65100
    style Output fill:#e8eaf6,stroke:#283593
```

---

## Agent Confidence Model

```mermaid
graph TD
    TaskInput["Task Input"] --> Steps["Processing Steps"]
    Steps --> StepConf["Per-Step Confidence<br/><i>0.0 - 1.0</i>"]
    StepConf --> Aggregate["Aggregate Confidence<br/><i>min(step_confidences)</i>"]
    Aggregate --> Check{"confidence<br/>>= floor?"}
    Check -->|Yes| Output["Return TaskResult<br/><i>auto-proceed</i>"]
    Check -->|No| HITL["Trigger HITL<br/><i>pause + notify</i>"]

    subgraph "Confidence Sources"
        LLM_Conf["LLM self-assessment"]
        Tool_Conf["Tool call success rate"]
        Data_Conf["Data completeness"]
        Match_Conf["Pattern match score"]
    end

    LLM_Conf --> StepConf
    Tool_Conf --> StepConf
    Data_Conf --> StepConf
    Match_Conf --> StepConf

    style TaskInput fill:#e1f5fe,stroke:#01579b
    style Aggregate fill:#fff3e0,stroke:#e65100
    style HITL fill:#fce4ec,stroke:#c62828
    style Output fill:#f1f8e9,stroke:#33691e
```

---

## Creating a Custom Agent

See [CONTRIBUTING.md](../CONTRIBUTING.md#creating-a-new-agent) for the step-by-step guide.

Key rules:
1. Must start in shadow mode (no exceptions)
2. Must have anti-hallucination rules
3. Must declare token scopes (principle of least privilege)
4. Cannot bypass HITL gates
5. Clone agents cannot elevate parent scopes

```mermaid
flowchart TD
    A["Define agent_type + domain"] --> B["Write system prompt<br/><i>prompts/{type}.prompt.txt</i>"]
    B --> C["Implement agent class<br/><i>extends BaseAgent</i>"]
    C --> D["Declare token scopes<br/><i>least privilege</i>"]
    D --> E["Add anti-hallucination rules"]
    E --> F["Write unit tests"]
    F --> G["Deploy in shadow mode"]
    G --> H["Collect 100+ samples"]
    H --> I{"Pass 6<br/>quality gates?"}
    I -->|Yes| J["Promote to production"]
    I -->|No| K["Iterate on prompt / logic"]
    K --> G

    style A fill:#e1f5fe,stroke:#01579b
    style G fill:#fff3e0,stroke:#e65100
    style I fill:#fce4ec,stroke:#c62828
    style J fill:#f1f8e9,stroke:#33691e
```

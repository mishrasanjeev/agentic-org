# Own the Agent, Open the Agent

> **Status: approved for implementation; delivery tracked in the phased plan (section 12).** This is the product requirements document for the bank-operated customer agent. The user-guide article [Customer agent](../user-guide/customer-agent.md) describes what ships today and how to try it; this document is the full requirement set, including the platform defects that gate the first customer-facing release.

**Product Requirements Document: a bank-operated customer agent for banks, insurers and NBFCs, built on AgenticOrg with Grantex as the authority service**

Version 1.0. Date: 11 October 2026. Owner: AgenticOrg product. Readers: BFSI product, risk, compliance and engineering leadership; AgenticOrg and Grantex engineering.

A bank keeps its customer relationship in the agent era by doing two things on one platform: shipping its own customer agent that is better than any outside agent at the bank's own services, and becoming the easiest institution for a customer's outside agent to connect to safely. Both run on the same authority, policy and evidence layer. The urgency is concrete. Revolut's AIR executes card controls and purchases with biometric step-up for roughly **13 million UK customers** ([FinTech Weekly](https://www.fintechweekly.com/news/revolut-air-ai-assistant-uk-customers-launch-2026)); Robinhood opened Trading and Banking MCP servers to any third-party agent on **27 May 2026** ([Robinhood newsroom](https://robinhood.com/newsroom/robinhood-is-now-open-to-agents/)); a general-purpose assistant gained read access to **more than 12,000 institutions** through Plaid in May 2026 ([TechCrunch](https://techcrunch.com/2026/05/15/openai-launches-chatgpt-for-personal-finance-will-let-you-connect-bank-accounts/)). Consumers already use AI for money guidance at scale (**55 per cent of US adults**, up from 10 per cent a year earlier) but only **18 per cent** would let it decide alone ([American Banker](https://www.americanbanker.com/news/more-americans-asking-ai-for-financial-advice-td-survey)), and they trust brand-owned agents **more than twice as much** as platform agents ([Accenture](https://www.accenture.com/gb-en/insights/consulting/talk-my-ai-agent)). That gap is the bank's opening. AgenticOrg supplies the agent runtime, banking pack, retrieval, guardrails, model gateway, operator override and hash-chained audit; Grantex supplies Agent Passports, grants, scopes, consent, revocation, decision grants and the trust registry. Most of these primitives exist but ship default-off, and the capability inventory found defects that must be fixed before any bank customer touches the product: no grant revocation path in AgenticOrg, middleware that never checks revocation, a pinned SDK three minor versions behind, an in-memory checkpointer that loses paused approvals, banking pack tools that silently drop, no end-customer channel, and spend caps that exist in name only. This PRD turns those findings into numbered requirements and a five-phase plan.

---

## 1. Executive summary and thesis

### 1.1 Own the agent, open the agent

The bank's own agent must be the best agent for the bank's customers, and the bank must be the easiest counterparty for a customer's outside agent to connect to safely. These are two front doors on one house, and the house is the authority, policy and evidence layer.

"Own the agent" means a bank-operated, bank-branded agent that sees every product the customer holds, every product the bank can offer, and (with consent) the customer's accounts elsewhere. It informs, recommends, prepares and, within a customer mandate, executes. It is accountable in the way only a regulated institution can be: it holds the customer's strong authentication factors, moves money inside its own ledger, opens disputes, freezes cards, decides credit, routes to a human, and is the single party answerable when something goes wrong ([Oliver Wyman](https://www.oliverwyman.com/our-expertise/insights/2026/jan/why-trust-gives-banks-edge-in-ai-economy.html)).

"Open the agent" means the bank publishes a governed MCP surface, recognises outside agents through Agent Passports and a trust registry, lets the customer grant them scoped authority, and lets the customer see and revoke every grant from one place. Robinhood and HSBC have shown the shape ([Robinhood newsroom](https://robinhood.com/newsroom/robinhood-is-now-open-to-agents/); [HSBC](https://www.hsbc.com/news-and-views/news/hsbc-news-archive/hsbc-launches-hsbcnio-digital-banking-solution-for-businesses)). A bank that refuses this door does not stop outside agents; it only loses the ability to govern them, because "it is the end user who chooses to hand payment credentials to an agent" ([Fintech Times](https://thefintechtimes.com/sibos-2026-day-three-before-the-agents-get-the-keys-the-industry-wants-trust-built-in/)).

### 1.2 Why one platform

Both doors need the same things: a verified identity for every agent, a grant that says what it may do and for whom, scopes and caps enforced at the point of execution rather than inside the model, instant revocation, a human-approval primitive for high-impact actions, and a tamper-evident record of every decision. Building these twice doubles the control surface and halves the evidence quality. AgenticOrg plus Grantex is the single platform for both: Grantex as the authority service, AgenticOrg as runtime, policy and evidence.

### 1.3 What this document asks for

Sections 2 to 7 set out the problem, goals, personas, scope, the open side and the control catalogue. Section 8 lays out the architecture and states honestly, per capability, whether AgenticOrg or Grantex has it, has it partially, or lacks it. Section 9 lists functional requirements with priorities and acceptance criteria. Sections 10 to 12 cover non-functional requirements, integrations and the phased plan. Section 13 names decisions needed. Appendices hold the standards table, glossary and recorded gaps.

---

## 2. Problem statement and market evidence

### 2.1 The trigger

After Sibos 2026 (Miami, 28 September to 1 October 2026; Frankfurt hosted the 2025 edition ([Swift](https://www.swift.com/news-events/events/sibos-2026-miami))), Eddy Rodriguez argued on LinkedIn that banks are moving AI into production just as customers begin to bring their own agents to the relationship. He cites Revolut's AIR assistant and Robinhood's agent work as early signs, and describes a financial assistant working 24/7 within the goals, risk tolerance and permissions a customer sets: finding a better savings rate, comparing card rewards against spending, evaluating ways to pay a mortgage faster, then taking approved actions. His conclusions: if agents make comparing providers and moving money easier, customer lifetime value gets harder to keep and banks must keep earning the relationship; risk and compliance become central to the experience (how a bank authenticates an agent, verifies on whose behalf it acts, enforces what it may do, and lets the customer see its actions and revoke access at once); customers may favour institutions that let their agents connect securely and get things done; and so banking infrastructure, meaning identity, permissions, secure connections and reliable execution, becomes the product. This PRD treats that argument as the hypothesis under test and sets out how a bank answers it with AgenticOrg and Grantex. It runs: a 24/7 financial assistant working within the goals, risk tolerance and permissions a customer sets can find a better savings rate, compare card rewards against spending, evaluate ways to pay a mortgage faster, then take approved actions. If agents make comparing providers and moving money easy, customer lifetime value becomes harder to keep and banks must keep earning the relationship. Risk and compliance move to the centre: how does a bank authenticate an agent, verify whose behalf it acts on, enforce what it may do, and let the customer see its actions and revoke access immediately? Customers may favour institutions that let their agents connect securely. In his phrase, "banking infrastructure is becoming the product". The evidence below shows the argument holds.

### 2.2 Bring-your-own-agent is live

Revolut's AIR began rolling out in the UK on 9 April 2026 and executes subscription management, card freezing, spending limits and in-app eSIM purchases; "sensitive actions require biometric approval before they execute" and AIR sees only what the customer sees in-app ([FinTech Weekly](https://www.fintechweekly.com/news/revolut-air-ai-assistant-uk-customers-launch-2026)). Robinhood productised inbound third-party agents: customers connect agents "from anywhere" to Trading and Banking MCP servers, the agent uses only funds in a ring-fenced agentic account, every trade sends a push notification, there is one-tap disconnect, and the Agentic Credit Card is a dedicated virtual card with a customer-set limit and optional approval of every purchase. Robinhood "does not control, supervise, monitor, recommend, or audit third-party agents" ([Robinhood newsroom](https://robinhood.com/newsroom/robinhood-is-now-open-to-agents/)). That disclaimer is the gap a regulated bank can fill: the same controls with accountability attached.

Incumbents are moving more slowly. DBS upgraded assistants serving more than **10 million users** with agentic actions on 28 July 2026, corporate first ([DBS](https://www.dbs.com/newsroom/DBS_Gen_AI_enabled_virtual_assistants_reach_10_million_customers_and_go_agentic)). Lloyds announced an in-app assistant able to "process a payment", tested with about 7,000 employees ([The Paypers](https://thepaypers.com/fintech/news/lloyds-banking-group-to-launch-its-agentic-ai-financial-assistant)). HSBC's HSBCnio, launched at Sibos on 29 September 2026, lets corporate clients' own AI tools query authorised data through an HSBC MCP server ([HSBC](https://www.hsbc.com/news-and-views/news/hsbc-news-archive/hsbc-launches-hsbcnio-digital-banking-solution-for-businesses)). Bank of America's Erica has **24.6 million active users** but its agentic work is at proof of concept ([Bank of America](https://newsroom.bankofamerica.com/content/newsroom/press-releases/2026/07/bank-of-america-enhances-ericaassist-with-generative-ai-to-help-.html)). SBI's chairman said in September 2026 that banks may need to move from KYC toward "knowing and authenticating the agent involved in a transaction" ([Indian Startup Times](https://www.indianstartuptimes.com/news/sbi-chairman-highlights-agentic-ai-trust-and-intelligent-inclusion-at-gff-2026/)).

General-purpose assistants now read bank data across more than 12,000 institutions via Plaid ([TechCrunch](https://techcrunch.com/2026/05/15/openai-launches-chatgpt-for-personal-finance-will-let-you-connect-bank-accounts/)) but none initiates bank-to-bank payments autonomously. The near-term disintermediation risk is therefore in advice, comparison and discovery, not execution (inference from the market notes). The bank's agent must win on advice first.

### 2.3 Disintermediation and customer lifetime value

McKinsey's 2026 review says "AI and stablecoins are dissolving stickiness" and notes profitability rests on the over-55 cohort, whose gen AI adoption now nearly matches millennials ([McKinsey](https://www.mckinsey.com/~/media/mckinsey/industries/financial%20services/our%20insights/global%20banking%20annual%20review/global-banking-annual-review-2026-precision-with-speed_final.pdf)). Accenture puts **22 per cent of US banks' pre-tax income** at risk from small disruption to loan and deposit rates, yet finds **71 per cent** would welcome an AI assistant in their primary bank's app ([Accenture](https://www.accenture.com/en/insights/banking/accenture-banking-trends-2026)). Oliver Wyman reports AI-driven traffic to US banking sites up **1,200 per cent in six months** ([Oliver Wyman](https://www.oliverwyman.com/our-expertise/insights/2026/jan/ai-agents-separate-banks-from-customers.html)). Celent proposes Agent IAM and Know Your Agent protocols to "recognise external agents knocking at the institution's doors" ([Retail Banker International](https://www.retailbankerinternational.com/news/identity-access-management-in-agentic-systems-celent-report/)). At Sibos, Temenos and Celent reported **56 per cent of retail banks** find retention harder and **25 per cent of consumers** recently considered switching ([Fintech Times](https://thefintechtimes.com/sibos-2026-day-two-banks-put-ai-agents-to-work-but-keep-the-judgement-for-people/)). No analyst has measured deposit outflows caused by consumer agents; the CLV case rests on direction, not a published model.

### 2.4 The trust ladder

Surveys describe one ladder: informing is welcome, recommending accepted by a third to a half, acting with approval by a small minority, acting unsupervised by almost nobody. Only **5 per cent** are comfortable with AI acting with their approval and **3 per cent** without; **42 per cent** would cap an agent at **$100** ([Biometric Update](https://www.biometricupdate.com/202610/consumer-trust-in-autonomous-agents-sinks-following-security-incidents)). Accenture's ladder runs 85, 74, 32 and 9 per cent from collaborate to fully autonomous ([Accenture](https://www.accenture.com/gb-en/insights/consulting/talk-my-ai-agent)). In the UK **43 per cent** trust their bank most for advice; **33 per cent** would trust AI guidance that explains its reasoning step by step and **24 per cent** if a human review process existed ([CFOtech UK](https://cfotech.co.uk/story/uk-consumers-trust-banks-over-ai-for-financial-advice)). In the US **75 per cent** want notification when AI is involved and **74 per cent** insist on override rights ([Crowdfund Insider](https://www.crowdfundinsider.com/2026/04/274513-fintech-apps-and-ai-tools-are-transforming-money-management-research/)). About one in five UK adults would use AI acting independently within goals they set ([FCA](https://www.fca.org.uk/publications/corporate-documents/mills-review)). India is the outlier: **70 per cent** open to AI deciding independently ([Biometric Update](https://www.biometricupdate.com/202610/consumer-trust-in-autonomous-agents-sinks-following-security-incidents)), yet **92 per cent** rank security first and **58 per cent** feel pushed toward bank-beneficial products ([CRN Asia](https://www.crnasia.com/india/news-network/news/banks-that-turn-customers-into-advocates-could-grow-revenue-considerably-faster-and-increase-share-of-wallet-accenture)). Business users tolerate more: **50 per cent** of US finance leaders would let agents initiate with approval and **15 per cent** would move banks if unsupported ([GlobeNewswire](https://www.globenewswire.com/news-release/2026/09/29/3370931/0/en/54-of-Finance-Leaders-Using-AI-Agents-Would-Seek-a-Workaround-if-Their-Bank-Can-t-Support-Them.html)).

The consequence: launch at inform, recommend and prepare; earn execution per task and per customer; make explanation and human review structural.

### 2.5 What regulators have said

No regulator has agent-specific binding rules yet; all keep accountability with the deployer. RBI FREE-AI (13 August 2025) sets seven sutras including "accountability rests with the entities deploying AI" and that humans "should be able to override AI" ([RBI](https://www.rbi.org.in/Scripts/PublicationReportDetails.aspx?ID=1306)). MAS's SAFR (3 July 2026) centres on agent identity and authorisation, checking actions against the user's mandate before execution, and auditability ([MAS](https://www.mas.gov.sg/news/media-releases/2026/mas-partners-industry-to-develop-safeguards-for-ai-agents-in-finance)). The FSB warns agent risks can "materialise at great speed" ([FSB](https://www.fsb.org/uploads/P100626.pdf)). HM Treasury asks how authentication, consent and liability must adapt for agentic payments ([GOV.UK](https://www.gov.uk/government/consultations/modernising-payment-services-regulation/modernising-payment-services-regulation-consultation)). Governor Waller reframed the question at Sibos as "does the agent have authority to pay for the buyer" ([Federal Reserve](https://www.federalreserve.gov/newsevents/speech/waller20260928a.htm)). A bank that builds the authority layer now is ready for whichever rules land first.

---

## 3. Goals, non-goals and success metrics

### 3.1 Goals

1. Give every retail and SME customer a bank-operated agent covering the institution's full service range, removing the reason to hand finances to a general-purpose agent.
2. Make the bank the easiest institution for a customer's outside agent to connect to, under bank-controlled identity, scopes, mandates and revocation.
3. Run both on one authority, policy and evidence layer so every agent action produces the same quality of record.
4. Fix the platform defects in section 8.3 before any customer-facing release.
5. Meet the convergent regulatory minimum (inventory, materiality tiering, meaningful human oversight, kill switch, tamper-evident audit, residency) in India, the UK, the EU and Singapore from day one.

### 3.2 Non-goals

- Replacing core banking, card management, payment switches or fraud engines; the agent calls them.
- Executing payments or mandates inside AgenticOrg; execution stays on the bank's rails, matching AgenticOrg's stated non-goal that its commerce runtime never creates orders, mandates, captures or refunds.
- Giving regulated personal advice without the relevant permission (section 7.2).
- Using the shared hosted instances of either product; both are single-region and foreign to India and the EU, so a bank deployment is self-hosted or dedicated in-country.
- Choosing a model vendor; the platform is vendor-neutral and the bank selects providers per use case through the model gateway.

### 3.3 Success metrics

Measurement follows the DBS pattern of control groups, the only published bank attribution of value (about **S$1 billion in 2025**) with that discipline ([Fintech News Singapore](https://fintechnews.sg/126153/ai/dbs-ai-investment-record-deposits-fee-income-2025/)). Containment is not resolution: one large bank's card chatbot plateaued at 18 per cent containment for 18 months ([Virtusa](https://www.virtusa.com/success-stories/over-30-percent-improvement-for-chatbot-containment-rates-at-leading-american-bank)) and Klarna reversed cost-first automation after quality fell ([Customer Experience Dive](https://www.customerexperiencedive.com/news/klarna-reinvests-human-talent-customer-service-AI-chatbot/)).

| Metric | Target (Phase 2 exit unless stated) | Baseline |
|---|---|---|
| Monthly active customers using the bank's agent | 25 per cent of digitally active base by Phase 3 exit | Unknown |
| Resolution without human (not containment) | 50 per cent of eligible intents; NatWest Cora reached 3.8 million of 7.1 million conversations fully digital in H1 2026 ([NatWest](https://investors.natwestgroup.com/~/media/Files/R/RBS-IR-V2/results-center/31072026/nwg-h1-2026-investor-factbook.pdf)) | Unknown |
| Repeat contact within 7 days of an agent interaction | Below 10 per cent | Unknown |
| Agent-attributed deposits, products held, retention | Measured against control groups quarterly; positive and significant by Phase 3 exit | Unknown |
| Out-of-mandate executions; within-mandate error rate | Zero; below 0.1 per cent of executed actions, all reimbursed under published policy | None yet |
| Revocation propagation, customer revoke to next action refused | p95 at most 2 seconds end to end | Grantex reports 100 to 506 ms service-side |
| Grants visible to the customer, inbound and bank agent | 100 per cent | 0 per cent (no customer surface today) |
| Approval latency, customer step-up and bank four-eyes | p95 under 60 seconds; under 4 hours | Unknown |
| Outside agent providers connected via bank MCP | At least three by Phase 4 | 0 |
| Audit chain verification | Daily pass, zero unexplained breaks | Chain off in every environment |

---

## 4. Personas and jobs to be done

| Persona | Jobs to be done | Success | Evidence |
|---|---|---|---|
| Retail customer | See money across banks; be warned before missed payments; get a better rate without hunting; pay, freeze, dispute in one place; understand why; stop the agent instantly | Whole-wallet view, nudges with "why this", one-tap prepared actions, biometric approval, one permissions page | 48 per cent open to proactive assistants paying bills and setting alerts ([American Banker](https://www.americanbanker.com/news/more-americans-asking-ai-for-financial-advice-td-survey)) |
| SME owner | Forecast cash, reconcile ledger to bank, pay vendors and GST on time, get working capital before a shortfall | Agent across ledger (Tally, Zoho, QuickBooks) and account; bulk approvals within limits | 50 per cent would let agents initiate with approval ([GlobeNewswire](https://www.globenewswire.com/news-release/2026/09/29/3370931/0/en/54-of-Finance-Leaders-Using-AI-Agents-Would-Seek-a-Workaround-if-Their-Bank-Can-t-Support-Them.html)) |
| Relationship manager | See what the agent said; take over with context; receive nudges worth a call | Supervisor view with takeover, summary and intent tag | DBS nudges also feed RMs ([Retail Banker International](https://www.retailbankerinternational.com/news/singapores-dbs-bank-leverages-ai-to-boost-intelligent-banking-capabilities/)) |
| Risk and compliance officer | Prove every action was within an authenticated mandate; show where humans sat; halt anything; produce evidence in hours | Agent inventory, materiality tier, kill switch, signed evidence package, override analytics | FSB SP10 and inventory attributes ([FSB](https://www.fsb.org/uploads/P100626.pdf)) |
| Bank platform team | Deploy in-country, integrate rails, route models by policy, bound costs, observe everything | Self-hosted AgenticOrg and Grantex, connectors, gateway with spend caps, traces and timelines | |
| Customer's external agent (principal) | Prove who I am and for whom; discover what I may do; get a scoped grant; act within it; learn when revoked | Agent Passport verification, MCP discovery, RAR-scoped grant, revocation feed | MCP authorization 2026-07-28 ([MCP](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)) |
| Regulator and auditor | Reconstruct any decision: instruction, mandate, data, steps, model version, approvals, warnings | Exportable hash-chained signed evidence package | Reconstruction standard ([Ashfords](https://www.ashfords.co.uk/insights/articles/when-an-ai-agent-makes-the-wrong-payment-who-carries-the-loss)) |

---

## 5. Product scope: the bank's agent

### 5.1 Service coverage

Deposits and savings, cards, unsecured and secured lending, mortgages, investments and wealth, insurance, domestic payments, remittances, and SME banking (current accounts, collections, payables, trade, GST and tax). Coverage of a domain means the agent can answer, explain, compare within the bank's shelf, prepare the action and, where permitted, execute it on the bank's own rails.

### 5.2 Autonomy ladder

Per task, per customer, earned and reversible, following the assistive, delegated and autonomous tiers practitioners converge on, with approval triggered by conditions rather than per-action sign-off ([Backbase](https://www.backbase.com/blog/agentic-ai-architecture-banking)). Capability is separated from permission ([arXiv](https://arxiv.org/pdf/2607.23438)).

| Level | Name | Behaviour | Examples | Launch default |
|---|---|---|---|---|
| L0 | Inform | Cited answers from bank data; no recommendation | Balance, due date, dispute status | On |
| L1 | Recommend | Personal suggestion with "why this" | Move idle balance to a higher-rate deposit | On where the advice regime allows |
| L2 | Prepare | Pre-filled action, one-tap confirm | Prepared transfer, card limit change | On |
| L3 | Execute with approval | Executes after biometric or passkey approval of the specific action | Pay bill, freeze card, raise dispute, buy fund units | Per task after Phase 2 |
| L4 | Execute within mandate | No per-action approval inside a customer-set mandate | Sweeps to savings, recurring bills, SME vendor payments | Off; opt-in per customer and task in Phase 3 |

Promotion to L4 needs an error-free history on the task, an explicit mandate and bank policy allowing L4 for the task class. Demotion is immediate on any irregularity, dispute or customer request and leaves the model unchanged.

### 5.3 Mandates and limits

A mandate is the customer's standing instruction and the agent's envelope: maximum per action, maximum per period, counterparty allow-list with Confirmation of Payee, product or category allow-list, time window, end date, and triggers for fresh confirmation. UK commercial VRP, live since 2 June 2026, already has the customer set a maximum per payment, a maximum per period and an end date, authorised once ([Payment Expert](https://paymentexpert.com/2026/10/05/vrps-future-bank-to-bank-payments/)). UPI Circle full delegation caps at **Rs 15,000 per month and Rs 5,000 per transaction** ([Razorpay](https://razorpay.com/blog/what-is-upi-circle/)), and NPCI is reported to be preparing a Unified Agent Protocol reusing those limits, not yet live ([Indian Pay Calculator](https://www.indianpaycalculator.in/govt-news/ai-agents-upi-payments-npci-unified-agent-protocol-2026)). The mandate is carried as Grantex `authorization_details` (purpose, tools, caps, budget), checked by `enforce()` at the tool boundary and again by the bank's rail, never inside the model. Mandates are re-confirmed at least every 90 days, matching UK open banking ([Lloyds Bank](https://www.lloydsbank.com/online-banking/open-banking.html)), and on any material change.

### 5.4 Whole-wallet view

**73 per cent** of customers globally and **87 per cent** in India deal with more than one bank ([Accenture](https://www.accenture.com/en/insights/banking/consumer-study-banking-advocacy-powering-growth); [CRN Asia](https://www.crnasia.com/india/news-network/news/banks-that-turn-customers-into-advocates-could-grow-revenue-considerably-faster-and-increase-share-of-wallet-accenture)). Aggregation is the general-purpose agent's structural advantage; the bank neutralises it by bringing aggregation inside. India's Account Aggregator rails had **252 million-plus accounts linked and 369 million-plus consents fulfilled** by December 2025 ([Sahamati](https://sahamati.org.in/varta-the-sahamati-newsletter-december-2025/)); the AA consent artefact records purpose, recipients and expiry, notifies on every use and is revocable at any time ([Vinod Kothari](https://vinodkothari.com/2020/02/nbfc-aa-consent-gateways/)). UK banks already offer in-app aggregation; in the US the Section 1033 rule is enjoined ([Consumer Finance Monitor](https://www.consumerfinancemonitor.com/2026/08/06/cfpb-sends-new-section-1033-open-banking-proposal-to-oira-for-review/)), so US deployments rely on commercial aggregators.

### 5.5 Proactive nudges

The best-attested results come from proactive delivery at volume, measured against controls and tied to one-tap execution. DBS delivered **1.2 billion nudges to 13 million customers** in 2024 ([Euromoney](https://wporigin.euromoney.com/article/2elrf2fh58cgsosco4cco84kw/awards/awards-for-excellence/asias-best-digital-bank-2025-dbs/)); Bank of America sent nearly **12 billion proactive alerts** in 2024 ([Bank of America](https://newsroom.bankofamerica.com/content/newsroom/press-releases/2025/02/digital-interactions-by-bofa-clients-surge-to-over-26-billion--u.html)). A two-million-person megastudy found generic reminders lifted deposit probability by 0.05 percentage points and personalising on recent behaviour roughly doubled the effect ([PMC](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12477529/)). The agent is a nudge-plus-execution engine with A/B measurement built in. Nudge classes: upcoming shortfall, idle balance, better rate on the bank's shelf, card reward mismatch, mortgage overpayment, insurance renewal, SME cash shortfall, GST due date. In India the explanation carries an explicit no-push-selling statement.

### 5.6 Channels

Bank app and web (embedded chat plus inline cards), WhatsApp, voice (IVR and in-app), and RM-assisted through a supervisor view. India is voice-first in regional languages plus WhatsApp, which RBI FREE-AI singles out as near-term high impact ([AuthBridge](https://authbridge.com/blog/rbis-free-ai-framework-key-highlights-summarised/)). All channels share one session, one memory and one authority context.

### 5.7 Explainability and disclosure

Every recommendation carries a "why this" panel: data used, rule or goal applied, alternatives, what the bank earns, how to challenge. Every interaction discloses AI involvement, consistent with RBI's awareness sutra ([RBI](https://www.rbi.org.in/Scripts/PublicationReportDetails.aspx?ID=1306)), SEBI's extent-of-AI disclosure duty ([BABL AI](https://babl.ai/sebi-introduces-new-investment-adviser-regulations-addressing-ai-usage-and-transparency/)) and HKMA's use, purpose and limitations disclosure ([Gibson Dunn](https://gibsondunn.com/wp-content/uploads/2024/09/hong-kong-monetary-authority-guidance-on-gen-ai-use-in-customer-facing-applications-and-use-of-ai-in-ml-tf.pdf)). Credit and pricing outcomes carry specific principal reasons.

### 5.8 Error recovery and redress

Where the rail allows, the agent reverses automatically and tells the customer. A human is reachable at any turn; **81 per cent** want human involvement in phone support ([American Banker](https://www.americanbanker.com/news/more-americans-asking-ai-for-financial-advice-td-survey)). The bank publishes a reimbursement policy, because consumers expect reimbursement after AI errors ([Payments Journal](https://www.paymentsjournal.com/consumers-are-putting-more-financial-decisions-in-ais-hands/)) and tribunals hold firms liable for their bots: "It makes no difference whether the information comes from a static page or a chatbot" ([American Bar Association](https://www.americanbar.org/groups/business_law/resources/business-law-today/2024-february/bc-tribunal-confirms-companies-remain-liable-information-provided-ai-chatbot/)). Complaints are tagged to separate within-mandate disputes, agent errors and out-of-mandate actions.

---

## 6. The open side: how outside agents connect

### 6.1 Agent identity and Know Your Agent

An outside agent presents an Agent Passport, an SD-JWT verifiable credential issued by an accredited issuer carrying the agent's DID, provider legal identifiers, software name and version, declared limits, verification level and a status reference. The bank is the relying party: it verifies the passport against the trust registry, checks issuer accreditation and attestation status, and assigns a trust level before considering a grant. The networks are converging on this shape: Visa, Mastercard and Ant International are building a shared Know Your Agent framework ([AI Business](https://aibusiness.com/generative-ai/safeguards-launched-for-ai-shopping-agents)); Mastercard's Acceptance Framework "registers and verifies AI agents before they are permitted to transact" ([Mastercard](https://www.mastercard.com/global/en/news-and-trends/stories/2025/agentic-commerce-framework.html)); Visa's Trusted Agent Protocol signs requests per RFC 9421 with an 8-minute window and nonce replay protection ([Visa](https://developer.visa.com/capabilities/trusted-agent-protocol/trusted-agent-protocol-specifications)). The bank's MCP surface accepts the same signatures so any agent the networks recognise, the bank recognises.

Maturity: Grantex's Agent Passport 1.0 is a draft with unpublished packages and passport-bound grants behind a default-off flag. There is no finished standard for "a human delegates to an AI agent"; OAuth 2.1 is a draft and the IETF WIMSE AIMS best practice dates from September 2026 ([IETF](https://datatracker.ietf.org/doc/html/draft-ietf-wimse-aims)). The bank builds on finished RFCs (9396, 8693, 9449, 8705, 9421) and FAPI 2.0 Final ([OpenID Foundation](https://openid.net/wg/fapi/specifications/)), with Grantex supplying the agent-specific layer.

### 6.2 Delegated authority grants

The bank-authenticated customer grants the outside agent scoped authority through an issuer-branded consent page on the bank's Grantex instance. The grant token is a JWT with `sub` the customer, `client_id` the agent, an `act` delegation chain and RFC 9396 `authorization_details` carrying purpose, tools, caps and budget ([RFC Editor](https://www.rfc-editor.org/rfc/rfc9396.html)). Live approval requires a passkey assertion bound to the request, so the authorisation server, not the agent's user interface, is the decision point. Tokens are audience-restricted, sender-constrained, short-lived and refreshed with single-use rotation; delegation depth defaults to 3.

### 6.3 MCP server exposure and scopes

The bank exposes customer services as an MCP server per the 2026-07-28 authorization specification: an OAuth 2.1 resource server publishing RFC 9728 metadata, validating audience, never transiting tokens issued for another resource, and challenging for minimum scope with step-up on `insufficient_scope` ([MCP](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)). Scopes follow the Grantex grammar `resource:action[:constraint]` and `tool:<connector>:<permission>[:<tool>]`, published in a bank scope registry with plain-language descriptions. Read scopes come first; write scopes require L3 or L4 authority and a mandate. Grantex `@grantex/mcp-auth` 4.x supplies the authorisation server, current-grant and decision verifiers; the bank supplies the human-principal resolver and durable storage.

### 6.4 Consent dashboard with instant revocation

The customer sees every agent with access, including the bank's own, with scopes, caps, last action and audit, and revokes with cascade to sub-grants. Grantex provides this through a principal session and `GET /v1/principal/grants`, `GET /v1/principal/audit` and `DELETE /v1/principal/grants/:id`. Revocation propagates through the revocation feed (snapshot, long-poll and SSE), signed webhooks and credential status lists; SDK `enforce()` checks revocation online by default and fails closed. The bank is the natural single pane because network agent tokens are revoked at the issuer and open banking mandates at the account-servicing institution (inference). Grant Management for OAuth, the only portable list-and-revoke standard, is still an Implementer's Draft ([OpenID Foundation](https://openid.net/wg/fapi/specifications/)), so the dashboard runs on Grantex grant records and adopts it when Final.

### 6.5 Agentic payments

Card-network agent tokens are live. Visa Intelligent Commerce binds agent tokens to a passkey-authenticated instruction and VisaNet enforces merchant and amount ([Visa](https://developer.visa.com/capabilities/visa-intelligent-commerce)); Mastercard Agent Pay has announced 2026 launches including India ([Mastercard](https://www.mastercard.com/news/ap/en/newsroom/press-releases/en/2026/mastercard-completes-korea-s-first-live-agentic-transactions-unlocking-trusted-ai-powered-commerce/)). As issuer the bank receives agent identity and intent through network messages and mirrors network controls into the Grantex grant so the customer sees one mandate. Account-to-account: UK VRP is live ([Payment Expert](https://paymentexpert.com/2026/10/05/vrps-future-bank-to-bank-payments/)) and the UK's first live agentic account-to-account payment in September 2026 used a Direct Debit mandate ([GoCardless](https://gocardless.com/blog/uk-first-agentic-payment-trussell)); BHIM went live with UPI Circle full delegation in November 2025 ([NPCI](https://www.npci.org.in/uploads/NBSL_Press_release_BHIM_Goes_Live_with_UPI_Circle_Full_Delegation_Enabling_Authorised_UPI_Payments_within_set_limits_7b308e643d.pdf)), while the agent-specific UPI protocol awaits NPCI rules. Protocol mandates: AP2 chains signed verifiable-credential mandates into a "non-repudiable, cryptographic audit trail" and is moving into FIDO Alliance working groups ([AP2](https://ap2-protocol.org/)); the Agentic Commerce Protocol is open-source beta ([GitHub](https://github.com/agentic-commerce-protocol/agentic-commerce-protocol)). The bank adopts these as intent-record formats when stable; Grantex's renderers for them are preview-only. Execution never happens in AgenticOrg: the agent prepares, the grant authorises, the bank's rail executes, the evidence layer records.

---

## 7. Risk, compliance and controls

### 7.1 Control catalogue

Regime status: RBI FREE-AI is a committee report ([RBI](https://www.rbi.org.in/Scripts/PublicationReportDetails.aspx?ID=1306)); FSB sound practices are a June 2026 consultation ([FSB](https://www.fsb.org/uploads/P100626.pdf)); MAS consulted on AI Risk Management Guidelines covering agents with a 12-month transition ([Allen & Gledhill](https://www.allenandgledhill.com/sg/publication/articles/31741/mas-consults-on-proposed-guidelines-for-artificial-intelligence-risk-management)); FCA Consumer Duty and the Mills Review apply in the UK ([FCA](https://www.fca.org.uk/publications/corporate-documents/mills-review)); EU AI Act high-risk obligations for creditworthiness apply from **2 December 2027** and Article 50 transparency from 2 August 2026 ([Hunton](https://www.hunton.com/privacy-and-cybersecurity-law-blog/eu-digital-omnibus-on-ai-enters-into-force)); SEBI makes advisers solely responsible for AI advice and signalled mandatory kill switches ([The Policy Edge](https://www.policyedge.in/p/sebi-signals-tiered-ai-rules-with-kill-switches-and-human-oversight)).

| ID | Control | RBI FREE-AI | FSB | MAS | FCA | EU AI Act | SEBI | Platform component (status) |
|---|---|---|---|---|---|---|---|---|
| C-01 | Agent inventory: identifier, tools, guardrails, permitted uses, model versions, tier | Governance | SP3, SP5 | Inventory | SM&CR | Art. 9 | AI records | Agent registry, model cards, inventory (exist, flag-gated) |
| C-02 | Materiality tiering; money and credit at top tier | Proportionate | SP5 | Impact, complexity, reliance | Proportionality | Annex III | Tiered | Risk tiers (exists, flag-gated) |
| C-03 | Meaningful human oversight at checkpoints; override analytics | Sutra 2 | SP10 | Oversight | Consumer Duty | Art. 14 | HITL | HITL grammar, approvals (exist); analytics (new) |
| C-04 | Kill switch at agent, model, tool, connector, all-agents; tested runbooks | Safety | SP10 | Stop | Resilience | Art. 14(4) | Kill switch | Operator override (exists, off); Grantex emergency stop (flag) |
| C-05 | Mandate enforced at execution layer: value, velocity, counterparty, category, time | Protection | Agentic risks | SAFR mandate check | PSRs consent | n/a | n/a | Grantex caps and budget (exist); `caps.enforce` (reserved, unimplemented) |
| C-06 | Per-agent identity, short-lived scoped credentials, no passthrough, model never holds credentials | Trust | SP11 | SAFR identity | n/a | n/a | n/a | Grantex DID, grants, vault references (exist) |
| C-07 | Revocation p95 at most 2 s, fail-closed verification | Protection | SP10 | SAFR | Customer control | n/a | n/a | Grantex feed (exists); AgenticOrg path (absent) |
| C-08 | Injection and goal-hijack defences: content isolation, semantic detector, tool allow-lists | Safety | SP11 | Controls | n/a | Art. 15 | n/a | Guardrails (exist, pattern-based, off); sandbox (exists) |
| C-09 | Advice and execution segregation | Understandable | SP8 | n/a | Advice boundary | n/a | IA rules | `validate_scopes` (exists); separate agent definitions (new) |
| C-10 | Reason codes for credit and pricing; disparate-impact testing | Fairness | SP8 | Fairness | Fair value | Art. 10 | Bias | Explanation field (exists); testing (new) |
| C-11 | AI disclosure at onboarding and every interaction | Sutra 2 | SP8 | Transparency | Consumer Duty | Art. 50 | Disclosure | Channel layer (new) |
| C-12 | Vulnerability detection routed to humans with evidence | People first | SP10 | n/a | FOS request | n/a | n/a | Escalation (partial); signals (new) |
| C-13 | Provider model update is a change event with regression gate | Governance | SP12 | Change mgmt | Contracts | Art. 9 | n/a | Evals v2 gate (exists, flag-gated); version pinning (new) |
| C-14 | Provider-failure fallback and tested exit plan | Resilience | SP12 | Third party | Resilience | DORA | n/a | Gateway failover (partial) |
| C-15 | Residency: India payment data in India, 24-hour offshore rule; attested in-region inference | 2018 directive | n/a | n/a | n/a | GDPR | n/a | Residency enforcement (exists, off) |
| C-16 | Tamper-evident audit: signed rows, hash chain, external anchors, WORM | Audit | SP8 | SAFR records | FOS | Art. 12 | Records | Audit chain (exists, off); Grantex audit and evidence (exist) |
| C-17 | Signed intent record per executed action | Protection | n/a | SAFR | PSRs authorisation | n/a | n/a | Grantex decision grants (exist); AgenticOrg binding (new) |
| C-18 | Incident reporting with AI taxonomy | Incidents | SP3 | n/a | SM&CR | Art. 73 | Reporting | Irregularity detection (Grantex exists; delivery absent) |
| C-19 | Transaction monitoring with agent flag, mandate id, velocity; CoP on new payees | Fraud | SP11 | n/a | APP regime | n/a | n/a | Bank fraud engine via connector (new) |
| C-20 | Vendor contracts: audit rights, change notice, incident SLAs, data-use limits | Outsourcing | SP12 | Third party | Mills | DORA Art. 28-30 | n/a | Contractual |

### 7.2 Advice boundary

A specific product or action proposed to an identified customer is a personal recommendation in every jurisdiction surveyed. Three compliant designs: hold the advice permission and run L1 under suitability; in the UK use targeted support, in force since **6 April 2026**, which allows suggestions to pre-defined segments and "actions in relation to existing products" ([Freshfields](https://riskandcompliance.freshfields.com/post/102mmq0/fca-finalises-targeted-support-rules-applications-now-open)); or constrain L1 to factual information plus execution of customer-originated instructions with hard output filters and audit evidence. The platform supports all three through per-agent guardrail rules; the bank chooses per domain and market. Where investments are in scope in India, SEBI's rules apply ([BABL AI](https://babl.ai/sebi-introduces-new-investment-adviser-regulations-addressing-ai-usage-and-transparency/)).

### 7.3 Model risk management

SR 26-2 excludes generative and agentic AI from US model-risk scope and points to the bank's own governance ([Federal Reserve](https://www.federalreserve.gov/supervisionreg/srletters/SR2602a1.pdf)); everywhere else the direction is stricter. The baseline adopted here is the convergent minimum: agent-level inventory, materiality tiering, pre-deployment and continuous evaluation on fixed test sets, provider updates treated as changes, documented meaningful oversight ([FSB](https://www.fsb.org/uploads/P100626.pdf)). ISO/IEC 42001 with ISO/IEC 42005:2025 impact assessments and NIST AI 600-1 form the audit scaffolding ([CMS](https://cms.law/en/gbr/legal-updates/iso-iec-42005-2025-a-new-blueprint-for-legal-and-commercial-leaders-navigating-ai-risk-and-governance); [NIST](https://www.nist.gov/node/1855536)). Reproducibility is evidenced by replayable logs, version pinning and periodic re-evaluation, not determinism.

### 7.4 HITL thresholds and kill switches

No regulator sets numeric thresholds; the bank sets them in a board-approved policy per task class. Proposed defaults: auto-execute below the mandate's per-action cap; customer step-up above it or for any new payee; bank four-eyes for high-value, new-payee or credit-affecting actions; always-human for vulnerability or fraud signals, regulatory requests and anything the HITL grammar cannot evaluate (which fails closed). Kill switches at five scopes with a 5-second cache and signed set and release; Grantex emergency stop at grant, agent, principal and developer scope; quarterly drills.

### 7.5 Liability positions

For a bank-operated agent the multi-party debate collapses onto the bank, which is PSP, adviser and deployer at once. Working positions from the legal commentary: within-mandate agent errors are the bank's loss; out-of-mandate actions are unauthorised transactions refundable under payment law; customer-caused losses (over-broad mandate set against warnings) are the only space where terms can shift risk, and only if mandate UX, warnings and records are demonstrably good ([Ashfords](https://www.ashfords.co.uk/insights/articles/when-an-ai-agent-makes-the-wrong-payment-who-carries-the-loss)). UK APP reimbursement (50/50 split, £85,000 cap) has no agent rule yet and the PSR consults in December 2026 ([Regulation Tomorrow](https://www.regulationtomorrow.com/2026/07/payment-fraud-falls-by-73m-following-psr-reimbursement-scheme/)); until then agent-initiated scam payments are treated as reimbursable like human-initiated ones. US Regulation E "assumes a transaction was either authorized or not" ([Worldpay](https://worldpay.com/insights/articles/agentic-commerce-liability-is-still-being-written)). The signed intent record (C-17) is the primary evidence in every case. For outside agents, the bank's terms follow the Robinhood pattern on scope but not on disclaimers: the bank remains accountable for what it executes within a valid grant; the agent provider is accountable under the relying-party agreement.

---

## 8. Architecture with AgenticOrg at the centre

### 8.1 Layered view

```
+------------------------------------------------------------------------------+
| CHANNELS                                                                     |
| Bank app and web (embedded widget) | WhatsApp | Voice | RM supervisor console |
| Outside agents via bank MCP server and A2A                                   |
+------------------------------------------------------------------------------+
| THE BANK'S AGENT RUNTIME (AgenticOrg)                                        |
| Customer session bound to bank identity | Agent graph (reason, validate_     |
| scopes, execute_tools, evaluate, hitl_gate) | Banking pack | Knowledge and   |
| RAG (citations, ACL, rerank, graph) | Memory (session, long-term) | Workflows|
| (13 step types incl. human_in_loop) | Nudge engine with A/B measurement      |
+------------------------------------------------------------------------------+
| AUTHORITY LAYER (Grantex)                                                    |
| Agent identity (DID, keys) | Agent Passport verification | Trust registry    |
| (accredited issuers, attestations, trust levels) | Grants with RAR           |
| authorization_details | Passkey-bound issuer-branded consent | Decision      |
| grants | Revocation feed, webhooks, emergency stop | Permission Dashboard    |
| DPDP consent records                                                         |
+------------------------------------------------------------------------------+
| POLICY LAYER (AgenticOrg)                                                    |
| Model gateway (routing, access, limits, cost-aware, in-region fence)         |
| Guardrails (input, retrieval, output, action) | Operator override            |
| Residency enforcement and provider attestations | Risk tiers, model cards    |
| PII pseudonymisation before model                                            |
+------------------------------------------------------------------------------+
| EVIDENCE LAYER (AgenticOrg and Grantex)                                      |
| Tracing | Run timelines | Signed audit rows | SHA-256 chain with anchors     |
| Model call digests | Signed evidence packages | SIEM export                  |
+------------------------------------------------------------------------------+
| INTEGRATION LAYER                                                            |
| Core banking | Cards | Payment rails (UPI, IMPS, NEFT, FPS, cards, VRP)      |
| Account Aggregator and open banking | Bureau | KYC and CKYC | CRM |          |
| Accounting (Tally, Zoho Books, QuickBooks) | Fraud engine | Remote MCP       |
+------------------------------------------------------------------------------+
```

### 8.2 Capability status by layer

**Exists** means implemented with tests in the repositories read; **Partial** means part exists, exists for one domain, or is default-off in a way that changes behaviour; **Absent** means nothing usable. The repositories' own BFSI matrix scores 205 capabilities at 30 Covered, 133 Partial, 42 Gap, and the inventory found it stale in both directions.

| Layer | Capability | Status | Inventory notes |
|---|---|---|---|
| Channels | Embeddable customer widget | Absent | No iframe, custom element or embed script |
| Channels | End-customer session | Absent | Every conversational entry point is operator-authenticated |
| Channels | Voice runtime | Partial | Twilio with provider-managed STT and TTS "ready"; others configuration-only; Indian providers absent |
| Channels | WhatsApp inbound for agents | Partial | Outbound exists; inbound only as admin-gated commerce Q&A; no verify handler |
| Channels | Supervisor view and takeover | Absent | Matrix FE-07 Gap |
| Channels | Localisation | Partial | English and Hindi UI strings only; no content translation |
| Runtime | Agent graph with HITL gate and fail-closed condition grammar | Exists | HITL at confidence below 0.88 or condition true |
| Runtime | Durable checkpointing | Partial | Defaults to in-memory; paused approvals lost on restart unless Postgres configured |
| Runtime | Banking pack | Partial | Templates for complaints, collections, KYC review, underwriting, reconciliation (`core/agents/packs/banking/`); `knowledge_base_search` drops silently; no servicing agent |
| Runtime | Intent recognition and multi-turn context | Absent / Partial | Keyword routing; history stored but not replayed |
| Runtime | Knowledge and RAG | Exists (flag-gated) | Hybrid retrieval, citations, ACL, rerank, graph; not bound as an agent tool |
| Runtime | Long-term customer memory | Absent | 24-hour chat history only |
| Runtime | Workflows and governed cases | Exists | 13 step types; fail-closed decision verifier |
| Authority | Agent identity, key rotation | Exists | `did:grantex`, 7-day rotation overlap |
| Authority | Agent Passport | Partial | Draft spec, unpublished packages, passport-bound grants flag-off and not delegable |
| Authority | Grants with RAR, delegation, caps, budget, purpose, region | Exists | 24-hour default lifetime, no maximum unless set |
| Authority | Passkey-bound issuer-branded consent | Exists | Self-hosted defaults off; sandbox can approve without principal authentication |
| Authority | Decision grants | Exists | `requires_decision` and `four_eyes` fail closed |
| Authority | Revocation feed, webhooks, emergency stop | Exists | Feed on; emergency stop behind flag; gateway and Go SDK do not check revocation |
| Authority | End-User Permission Dashboard | Exists | Principal-session link; no standalone login |
| Authority | Trust registry, accredited issuers, attestations | Exists (flag-gated) | Five trust marks; public endpoints off; federation not implemented |
| Authority | DPDP consent records | Exists | Withdrawal-revokes-grant behind flag; consent receipts absent |
| Authority | AgenticOrg consumption of Grantex | Partial | Pooled 15-minute run grants and `enforce()` on SDK 0.5.1; no principal sessions, decision grants, passports, attestations or feed |
| Policy | Model gateway | Exists (off) | Routing, access, tiers, cost-aware, in-region fence, digest records; sidecar calls bypass it; limits are concurrency and rate only |
| Policy | Guardrails | Exists (off) | Four stages, six detectors; injection and grounding pattern-based; toxicity classifier dependency absent |
| Policy | Operator override | Exists (off) | Eight scopes, 5-second cache, signed audit; no UI |
| Policy | Residency enforcement | Exists (off) | Attestations are the administrator's own record; hosted deploy foreign by default |
| Policy | Risk tiers, model cards, inventory | Exists (off) | APIs, no UI; `decisions.required` and `caps.enforce` have no reader |
| Policy | PII pseudonymisation | Exists | Aadhaar, PAN, GSTIN, UPI recognisers |
| Evidence | Signed audit rows | Exists | HMAC with the general app secret; no KMS or HSM |
| Evidence | Hash chain, anchors, verify | Exists (off) | Never enabled in any environment; no WORM or export |
| Evidence | Tracing and timelines | Partial | Workflow, hitl and auth spans have no callers; no collector |
| Evidence | Grantex evidence packages | Exists | `enforce()` emits no audit record; Enforce Log backend missing |
| Integration | Account Aggregator | Partial | Finvu client; fetch paths unverified |
| Integration | Accounting (Tally, Zoho Books, QuickBooks, NetSuite, SAP) | Exists | Real clients |
| Integration | GSTN, Income Tax, TRACES | Partial | GSTN real; Income Tax filing blocked; TRACES offline |
| Integration | Core banking, cards, UPI switch, KYC, bureau | Absent | No connector files |
| Platform | RLS, RBAC, scoped keys, vault | Exists | Effective only under a non-BYPASSRLS role, unchecked at runtime |
| Platform | Spend caps | Partial | Per-agent cap on one endpoint; no token or spend limits in gateway |
| Platform | In-country hosting | Partial | Self-hostable; hosted single-region foreign; Grantex has no DR or backup runbook |

### 8.3 Critical defects as explicit requirements

Each is P0 and a Phase 0 exit criterion.

| ID | Defect found | Requirement |
|---|---|---|
| DEF-01 | No grant revocation path in AgenticOrg: `TokenPool.revoke_token()` has no callers; pause and retire return `"token_revoked": true` without revoking | Pause and retire revoke through Grantex and the pool; the response reflects the real result |
| DEF-02 | Token pool never revokes; grants only expire | Pool subscribes to the revocation feed and evicts revoked tokens |
| DEF-03 | Middleware checks neither revocation nor agent status; a paused or retired agent's unexpired token still authenticates | Verify current grant state and refuse paused or retired agents; fail closed |
| DEF-04 | Pinned `grantex==0.5.1` predates purpose, cap and reason-code APIs; Grantex publishes 0.7.x | Re-pin and verify purpose, cap, decision and region enforcement end to end |
| DEF-05 | In-memory checkpointer by default; paused approvals lost on restart | Postgres sealed checkpointer mandatory; startup refuses `memory` outside development |
| DEF-06 | Banking pack tools not bound; `knowledge_base_search` dropped silently; no retrieval tool at all | Bind retrieval as an agent tool; unresolved pack tools fail loudly at creation |
| DEF-07 | No end-customer channel or widget | Customer session layer, embeddable widget, auth-exempt customer routes |
| DEF-08 | Limits without spend caps; `caps.enforce` reserved but unimplemented | Spend caps on every run path; token and cost limits in the gateway; `caps.enforce` wired to Grantex caps |
| DEF-09 | `grants.enforce_closed` defaults `off` | Bank profile forces `deny`; `off` refused in strict runtimes |
| DEF-10 | Audit signed with the general app secret; chain off everywhere | Dedicated KMS or HSM key; chain on with daily verification and anchor export |
| DEF-11 | A2A tasks accept any authenticated credential unless a flag is set | Route scopes on by default for A2A and MCP |
| DEF-12 | Agent delegation endpoint returns `delegated` on failure and discards the token | Return the real outcome; store the delegated token or fail |
| DEF-13 | Tenant admins can switch on live `money` and `filing` capability | Move `safety.live_capability.*` to operator-reserved keys |
| DEF-14 | Guardrail hooks and enforcement off; detector pattern-based; classifier absent | Both on; semantic detector; working classifier; multilingual coverage |
| DEF-15 | Grantex gateway and Go SDK skip revocation; status lists unsigned; missing list treated as not revoked | Revocation-aware verification everywhere; signed lists; fail closed |
| DEF-16 | Grantex `enforce()` emits no audit; Enforce Log backend missing | Enforcement decisions written to the hash-chained audit; real endpoint |

---

## 9. Functional requirements

P0 ships before any customer-facing release; P1 before execute with approval; P2 before mandates and ecosystem.

### 9.1 Customer identity, session and consent

| ID | Requirement | Priority | Acceptance criteria | Component (status) |
|---|---|---|---|---|
| FR-01 | Bind the bank-authenticated customer to a Grantex principal and an AgenticOrg session | P0 | Principal id identical across session, grants and audit rows | Session layer (new); Grantex principal (exists) |
| FR-02 | Customer permissions page listing all agents, scopes, caps, last action, audit | P0 | Shows bank agent and outside agents from principal endpoints; loads under 2 s | Permission Dashboard (exists); embedded rendering (new) |
| FR-03 | One-tap revoke with cascade; p95 at most 2 s to next refused action | P0 | Tool call after revoke refused with `grant_revoked`; measured end to end | Grantex revocation (exists); DEF-01 to DEF-03 (new) |
| FR-04 | Passkey-bound consent for grant creation and L3 approval on an issuer-branded page | P0 | Live approval refused without bound WebAuthn assertion | Grantex consent (exists); branding (partial) |
| FR-05 | AI disclosure at first use and every interaction; acknowledgement retained | P0 | Acknowledgement in audit; transcripts carry AI marker | Channel layer (new) |
| FR-06 | AA and open banking consent with per-use notification and partial revocation | P1 | Artefact created, used, revoked through bank's AA client | AA connector (partial) |

### 9.2 Conversation and autonomy

| ID | Requirement | Priority | Acceptance criteria | Component (status) |
|---|---|---|---|---|
| FR-07 | Banking intent model, at least 60 intents across nine domains | P0 | At least 90 per cent accuracy on held-out set; unknowns route to clarification or human | New |
| FR-08 | Multi-turn context replayed within session and across channels | P0 | Follow-ups resolve earlier references; session continues app to WhatsApp | Memory (partial) |
| FR-09 | Long-term customer memory, visible and editable | P1 | Customer views, edits, deletes items; pseudonymised before model calls | New |
| FR-10 | Autonomy level per task and customer, promotable and demotable, default L0 to L2 | P0 | L3 and L4 refused unless enabled; demotion immediate on irregularity | Action policy (partial) |
| FR-11 | "Why this" on every recommendation | P0 | Present on 100 per cent of L1 outputs; grounding check passes | Explanation (exists); grounding (partial) |
| FR-12 | Prepared actions with one-tap confirm | P0 | Confirm executes via rail; cancel leaves no side effect | Workflows and connectors (new) |
| FR-13 | Execute with approval via passkey push; signed intent record per action | P1 | Approval delivered within 5 s; executes only on approval; record chained | Decision grants (exist); binding (new) |
| FR-14 | Guaranteed human hand-off with summary and intent tag | P0 | Available at every turn; RM sees summary in supervisor view | Escalation (partial); supervisor view (new) |
| FR-15 | Nudge engine with control arms and one-tap execution | P1 | Each nudge has a control; conversion and value reported | New |
| FR-16 | Voice and WhatsApp in Hindi, English and two further Indian languages | P1 | Intent accuracy within 5 points of English; spoken PII redacted | Voice (partial); speech gaps (absent) |
| FR-17 | Error recovery: automatic reversal where possible, explanation, reimbursement link | P1 | Every disputed action produces a case with intent record attached | New |

### 9.3 Mandates, limits and execution

| ID | Requirement | Priority | Acceptance criteria | Component (status) |
|---|---|---|---|---|
| FR-18 | Mandate schema: per-action cap, period cap, counterparty and category allow-lists, window, end date, re-confirm triggers | P1 | Carried in `authorization_details`; outside actions refused with `cap_exceeded` or `purpose_not_allowed` | Grantex caps (exist); `caps.enforce` (new) |
| FR-19 | Mandate enforced at tool boundary and at bank rail | P1 | Both checks logged; disagreement raises irregularity and halts | ToolGateway (exists); rail connector (new) |
| FR-20 | Confirmation of Payee on every new payee | P1 | No payment prepared to an unverified payee | CoP via connector (new) |
| FR-21 | 90-day and change-triggered mandate re-confirmation | P2 | Expired mandate demotes to L3 | Grant expiry (exists); scheduler (new) |
| FR-22 | Spend caps on every run path and in the gateway, in tokens and currency | P0 | Chat, A2A, MCP, workflow, voice refused at cap; alerts at 50 and 80 per cent | DEF-08 (new) |
| FR-23 | L4 execution for sweeps, recurring bills, SME payables | P2 | Executes within mandate; every execution notified; kill switch demotes | Workflows (exist); rails (new) |
| FR-24 | Agent channel flag, mandate id and provenance to the fraud engine | P1 | Every agent transaction carries the fields | Fraud connector (new) |

### 9.4 Outside agents

| ID | Requirement | Priority | Acceptance criteria | Component (status) |
|---|---|---|---|---|
| FR-25 | Bank MCP server per the 2026-07-28 spec | P1 | Conformance passes; foreign-audience token refused; step-up on `insufficient_scope` | mcp-auth (exists); bank server (new) |
| FR-26 | Agent Passport verification with trust level gating scopes | P2 | Unverified passport gets no write scope; revoked attestation refused within 2 s | Passport and registry (partial) |
| FR-27 | Relying-party registration with accredited-issuer attestation and signed agreement | P2 | Registered, attested and contracted before first grant | Registry (exists) |
| FR-28 | Published scope registry; read before write | P1 | Public page; write scopes need L3 or L4 and a mandate | Scopes (partial, static) |
| FR-29 | RFC 9421 signed-request verification, 8-minute window, nonce replay protection | P2 | Replayed or stale signature refused | agent-httpsig (partial) |
| FR-30 | Outside-agent activity feed, push per action, one-tap disconnect | P1 | Every inbound action notifies; disconnect cascades | Dashboard (exists); notifications (new) |
| FR-31 | Network agent-token mirror in Grantex | P2 | Revoking either side revokes both | New |
| FR-32 | A2A and MCP route scopes on by default | P0 | Unscoped credential refused | DEF-11 (exists, flag) |

### 9.5 Risk, controls and evidence

| ID | Requirement | Priority | Acceptance criteria | Component (status) |
|---|---|---|---|---|
| FR-33 | Inventory entry per agent: tools, guardrails, permitted uses, model versions, tier | P0 | Stale entry blocks promotion | Registry, inventory, tiers (exist, flag-gated) |
| FR-34 | Kill switch at five scopes with signed audit and quarterly drill | P0 | Halts within 5 s; drill recorded | Operator override (exists, off); UI (new) |
| FR-35 | Board-approved HITL thresholds; unevaluable conditions fail closed | P0 | Versioned policy; monthly override analytics | HITL grammar (exists); analytics (new) |
| FR-36 | Advisory agents hold no execution tools | P0 | Static check at creation; violation refused | Agent config (new) |
| FR-37 | Guardrails on in four stages with semantic injection detection, multilingual | P0 | Adversarial set passes incl. paraphrased and non-English overrides | DEF-14 (partial) |
| FR-38 | Untrusted-content isolation for all retrieved and tool content on customer agents | P0 | Context guard active; leak raises error | Sandbox (exists, scoped to cases) |
| FR-39 | Hash chain on, KMS or HSM key, daily verify, anchor export, WORM | P0 | Verify passes daily; anchors in SIEM | DEF-10 (exists, off) |
| FR-40 | Signed evidence package per interaction (identity and SCA event, mandate version, transcript with retrieved content and tool calls, model and prompt hash, policy decisions, approvals, intent) | P1 | Exportable within one hour; signature verifies; PII pseudonymised | Grantex packages (exist); AgenticOrg package (stub) |
| FR-41 | Gateway on with in-region fence, attestations, cost-aware routing, second-provider failover on every path | P0 | Out-of-region provider refused; failover tested; sidecar calls routed | Gateway (exists, off); failover (partial) |
| FR-42 | Provider update as change with regression gate | P1 | Version recorded per run; new version passes dataset before promotion | Evals v2 (exists, flag-gated) |
| FR-43 | Residency: India payment data in India, 24-hour offshore rule; EU and UK equivalents | P0 | Enforcement on; attestations evidenced | Residency (exists, off) |
| FR-44 | Vulnerability signals routed to humans with evidence | P1 | Taxonomy defined; routed cases sampled monthly | New |
| FR-45 | Reason codes and quarterly disparate-impact testing for credit and pricing | P1 | Specific reasons; results in inventory | New |
| FR-46 | Irregularity alerts delivered to bank operations | P1 | Alert delivered and acknowledged | Detection (exists); delivery (absent) |
| FR-47 | Complaints taxonomy: within-mandate dispute, agent error, out-of-mandate | P1 | Every complaint tagged; monthly report | New |

---

## 10. Non-functional requirements

| Area | Requirement |
|---|---|
| Security | Per-agent identity with short-lived, audience-restricted, sender-constrained tokens; the model never holds credentials; vault references redeemed by the gateway; OWASP Top 10 for Agentic Applications 2026 mitigations mapped and tested ([OWASP](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026)); annual penetration test; signing keys in KMS or HSM with publish-then-sign rotation |
| Privacy | DPDP consent, notice, withdrawal, grievance and erasure through the Grantex DPDP module; core DPDP Rules obligations about 18 months after the 14 November 2025 notification ([Storyboard18](https://www.storyboard18.com/digital/breaking-govt-notifies-final-dpdp-rules-sets-staggered-rollout-with-key-obligations-for-data-fiduciaries-84201.htm)); GDPR for EU customers; pseudonymisation before every model call; no training on customer data, contractually and technically |
| Residency | India payment system data stored only in India with the 24-hour offshore round-trip rule ([Treelife](https://treelife.in/?p=16560)); in-region or attested inference; EU and UK data in region; both products self-hosted in-country |
| Availability | 99.9 per cent inform and recommend; 99.95 per cent execute paths; degraded mode falls back to inform-only with notice; Postgres checkpointer; monthly provider failover test |
| Latency | First token under 2 s p95; prepared action under 3 s; approval push under 5 s; revocation under 2 s p95; voice turn under 1.5 s |
| Cost | Per-interaction cost budgeted; cost-aware routing; spend caps in tokens and currency per agent, tenant and provider; no fail-open metering |
| Observability | Spans for every model call, tool call, retrieval, approval and auth decision with a deployed collector; run timelines; alert rules; live feed wired |
| Auditability | Every action reconstructable; retention for the longest applicable period (six years SEC 17a-4 and five years SEBI cited by commentary, to be confirmed against rule text); WORM storage |
| Accessibility | WCAG 2.2 AA for widget and permissions page; voice as first-class channel; plain-language explanations tested with customers |
| Multilingual | Hindi and English at launch; two further Indian languages in Phase 1; guardrails and intents evaluated per language; content translation added |

---

## 11. Data and integration requirements

Connector realness follows the inventory's code reading; the repository warns that "a connector listed in source is not proof of a live customer integration".

| System | Purpose | Exists | Must be built |
|---|---|---|---|
| Core banking | Balances, transactions, standing instructions, servicing, product opening | Absent | Read and write connector per core platform, idempotent, residency-gated |
| Cards and tokenisation | Controls, limits, freeze, agent-token lifecycle, disputes | Absent | Card management connector; network agent-token integration as issuer |
| Payment rails (UPI, IMPS, NEFT, RTGS, FPS, SEPA, cards, VRP) | Prepared and approved payments; mandates | Absent | Rail connectors via payment hub; CoP; mandate create and cancel; agent flag |
| Account Aggregator | Whole-wallet; cash-flow underwriting | Partial (Finvu client; fetch paths unverified) | Certified FIP and FIU flows; per-use notification; partial revocation |
| Open banking (UK, EU) | Whole-wallet; VRP | Absent | AISP and PISP integration or TPP partner |
| Credit bureau | Eligibility, offers, reason codes | Absent | Purpose-bound connector |
| KYC, CKYC, PAN, Aadhaar, DigiLocker | Onboarding and re-KYC | Absent (format checks only) | Verification connectors under bank KYC policy |
| CRM | Context, RM assignment, leads | Exists (Salesforce, HubSpot) | Bank CRM adapter; lead routing |
| Accounting (Tally, Zoho Books, QuickBooks, NetSuite, SAP, Xero) | Reconciliation, payables, forecasts | Exists except Xero | Xero; connected-banking flows from the agent |
| GST and tax | SME filing nudges | Partial (GSTN real; Income Tax blocked; TRACES offline) | Production filing path; e-invoice |
| Fraud and transaction monitoring | Agent-channel scoring | Absent | Event feed with agent features |
| Messaging and voice | WhatsApp, SMS, IVR | Partial (outbound; inbound only commerce; Indian voice providers absent) | General inbound WhatsApp with verify handler; Indian telephony and speech |
| Knowledge sources | Product terms, policies, notices | Exists (RAG with citations, ACL) | Bind as agent tool; ingestion from bank content systems |
| SIEM and evidence export | Audit, anchors, packages | Exists on Grantex; absent server-side on AgenticOrg | Server-side export from AgenticOrg |

Data classes: customer identifiers and payment data (India-resident, pseudonymised before model calls); transcripts (encrypted, PII-minimised, retained per jurisdiction); mandates and grants (Grantex store, hash-chained); model call records (digests only); evidence packages (signed, WORM). The bank's product shelf must be machine-readable and complete so it is comparable inside its own agent and discoverable by outside agents.

---

## 12. Phased delivery plan

Quarters are relative to programme start. Phases are workstreams with gates, not calendar promises.

| Phase | Workstreams | Dependencies | Regulatory gate | Exit criteria |
|---|---|---|---|---|
| 0: foundations and defects (Q1) | Close DEF-01 to DEF-16; bank deployment profile forcing `deny` enforcement, Postgres checkpointer, guardrails, gateway, residency, audit chain and route scopes on; re-pin Grantex SDK; in-country self-hosting with KMS or HSM; customer session layer; retrieval bound as tool; intent model v1 | Bank identity provider; in-country infrastructure; Grantex fixes to verifiers and enforce-time audit | Board-approved AI policy and tiering; inventory populated; ISO/IEC 42005 impact assessment | All DEFs closed with tests; revocation under 2 s p95 end to end; daily chain verification passing; penetration test complete |
| 1: inform and recommend (Q2 to Q3) | Widget; WhatsApp inbound; voice in Hindi and English; L0 and L1 across deposits, cards, lending, payments status, insurance, SME; "why this"; disclosure; whole-wallet via AA or open banking partner; human hand-off with supervisor view; nudge engine with controls; kill switch drills | Core banking read connector; AA production flows; knowledge ingestion; advice regime decision per domain | Advice boundary sign-off; disclosure approved; vulnerability routing evidenced; UK targeted support if used | Intent accuracy at least 90 per cent; resolution and repeat-contact reported with controls; zero red-team guardrail bypasses; complaint taxonomy live |
| 2: execute with approval and open MCP (Q3 to Q4) | L2 and L3 for transfers, bills, card controls, disputes, top-ups, SME vendor payments; signed intent record; decision grants wired; fraud agent features; CoP; bank MCP server and scope registry; outside-agent feed and disconnect; permissions page with inbound and bank agents | Payment hub and card connectors; fraud engine; relying-party agreement; mcp-auth principal resolver | Payments authorisation legal opinion per jurisdiction; reimbursement policy published; DPDP flows live | Zero out-of-mandate executions in pilot; approval latency met; MCP conformance passes; one outside provider in sandbox |
| 3: mandates and agentic payments (Q5 to Q6) | Mandate schema and L4 for sweeps, bills, SME payables; 90-day re-confirmation; network agent-token mirror; UK VRP as mandate rail; UPI Circle-aligned limits pending NPCI rules; Agent Passport and registry gating; per-interaction evidence packages within one hour | Network programmes in bank markets; NPCI agent rules (not live); HMT response (targeted Q4 2026) and PSR consultation (December 2026) ([GOV.UK](https://www.gov.uk/government/publications/payments-forward-plan/payments-forward-plan-accessible-version)) | Supervisory engagement on L4; EU creditworthiness cases assessed ahead of 2 December 2027; SEBI compliance where investments in scope | L4 live for two task classes with error rate below 0.1 per cent; mandate verified at tool boundary and rail; single customer-visible mandate |
| 4: ecosystem (Q7 onward) | Accredited-issuer programme; relying-party onboarding; RFC 9421 verification; AP2 or ACP intent formats as they stabilise; more Indian languages and voice providers; SME ledger-to-bank automation incl. Xero; machine-readable product shelf | Standards maturity (Grant Management, passport specs, OpenID Federation); FIDO AP2 work | Ongoing supervisory reporting | Three relying-party providers live; measured retention and product effect versus control; regulator evidence requests within SLA |

---

## 13. Risks, open questions and decisions needed

### 13.1 Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Default-off governance stays off in production | High | Severe | Bank profile hard-codes strict configuration; startup refuses unsafe defaults |
| Revocation gap lets a revoked agent act | High today | Severe | DEF-01 to DEF-03 as Phase 0 exits; end-to-end measurement |
| Pattern-based injection detection misses paraphrased or non-English attacks | High | Severe | Semantic detector; content isolation on all customer agents; mandate enforced at the rail so a hijacked agent cannot exceed the instruction |
| Liability rules change mid-programme (HMT, PSR, NPCI, EU) | Medium | High | Signed intent records from day one; narrow mandates until rules land; legal review per gate |
| Provider concentration and update risk | Medium | High | Vendor-neutral gateway, second provider, regression gate ([FSB](https://www.fsb.org/uploads/P100626.pdf)) |
| Advice boundary crossed by L1 | Medium | High | Per-domain regime, output filters, audit sampling, targeted support where available |
| Trust falls after a visible incident; it already fell after security incidents ([Biometric Update](https://www.biometricupdate.com/202610/consumer-trust-in-autonomous-agents-sinks-following-security-incidents)) | Medium | High | Launch L0 to L2; reimbursement policy; transparent incident communication |
| Integration partner outages; an accounting integration was "temporarily disabled" at a bank's request ([Zoho](https://www.zoho.com/in/books/kb/icici/icici-bank-integration.html)) | Medium | Medium | Degrade to AA or open banking feeds; connector health in inventory |
| Containment rises while quality falls | Medium | Medium | Resolution, repeat-contact and reimbursement as north-star metrics; guaranteed human path |
| Grantex in-country hosting has no DR or backup runbook | High | High | Bank-operated DR and backup as Phase 0 deliverable with RPO and RTO |

### 13.2 Open questions

1. Whether model inference on payment data is "processing" under RBI's 24-hour offshore rule; no source addresses it. Default: in-region inference.
2. How agent-initiated payments are classified under PSRs 2017 and PSD2, and how the PSR caution standard applies when the bank's own agent chose the action; pending HMT and PSR.
3. NPCI's final limits, liability and launch date for agent payments on UPI.
4. Whether SEC 17a-4 treats prompts and responses as records; the bank retains them regardless.
5. Which advice regime applies per domain and market, including UK targeted support.
6. Grantex grant lifetime defaults for the bank profile (service default 24 hours, no maximum unless set).
7. Reconciling the two conflicting functional coverage totals in the repositories' BFSI documents.

### 13.3 Decisions needed from the bank

- Approve the autonomy policy: task classes eligible for L3 and L4, default caps, four-eyes thresholds.
- Choose the advice regime per domain and market.
- Confirm in-country self-hosting of both products with bank-operated DR and backup.
- Select model providers per use case through the gateway and accept the second-provider requirement.
- Approve the reimbursement policy and the relying-party agreement.
- Nominate accountable owners under SM&CR or equivalent for the agent, mandates and kill switch.

### 13.4 Decisions needed from the AgenticOrg product owner

- Commit Phase 0 engineering to DEF-01 to DEF-16 ahead of feature work and publish a bank deployment profile.
- Raise the Grantex SDK pin; implement the reserved `caps.enforce` and `decisions.required` flags.
- Consume Grantex principal sessions, decision grants, DPDP records, passports and the revocation feed natively in AgenticOrg (recommended) rather than leaving them to bank integration.
- Build the customer session layer, widget, banking intent model, supervisor view and long-term memory as platform features.
- Publish the Agent Passport packages; move passport-bound grants, registry public endpoints and lifecycle states out of default-off for the bank profile; fix passport-bound grant delegation.
- Add enforce-time audit records and the Enforce Log backend in Grantex; sign status lists and fail closed on missing lists.
- Reconcile the BFSI coverage matrix with code and keep it current; it is what a bank's due diligence will read.
- Decide the India and EU hosting roadmap for Grantex (demand-gated for H1 2027) or formally position self-hosting as the bank option.

---

## 14. Appendices

### Appendix A: standards and regulation reference

| Item | Body | Status and maturity | Date | Relevance |
|---|---|---|---|---|
| OAuth 2.1 | IETF | Internet-Draft (draft-13) | 2026 | Base for MCP authorization ([IETF](https://datatracker.ietf.org/doc/html/draft-ietf-oauth-v2-1-13)) |
| RFC 9396 Rich Authorization Requests | IETF | RFC | May 2023 | `authorization_details` for mandates ([RFC Editor](https://www.rfc-editor.org/rfc/rfc9396.html)) |
| RFC 8693 Token Exchange | IETF | RFC | Jan 2020 | `act` delegation chain ([RFC Editor](https://www.rfc-editor.org/rfc/rfc8693.html)) |
| RFC 9449 DPoP; RFC 8705 mTLS | IETF | RFC | 2023; 2020 | Sender-constrained tokens ([RFC Editor](https://www.rfc-editor.org/rfc/rfc9449.html); [RFC Editor](https://www.rfc-editor.org/rfc/rfc8705.html)) |
| RFC 9421 HTTP Message Signatures | IETF | RFC | Feb 2024 | Agent request signing ([RFC Editor](https://www.rfc-editor.org/rfc/rfc9421.html)) |
| RFC 9728 Protected Resource Metadata | IETF | RFC | Apr 2025 | MCP discovery ([RFC Editor](https://www.rfc-editor.org/rfc/rfc9728.html)) |
| FAPI 2.0 Security Profile and Message Signing | OpenID Foundation | Final | 2025 | Financial-grade baseline ([OpenID Foundation](https://openid.net/wg/fapi/specifications/)) |
| Grant Management for OAuth 2.0 | OpenID Foundation | Implementer's Draft 1 | Pending | Portable list and revoke ([OpenID Foundation](https://openid.net/wg/fapi/specifications/)) |
| MCP Authorization | Agentic AI Foundation | Revision 2026-07-28 | Jul 2026 | Bank MCP server contract ([MCP](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)) |
| WIMSE AIMS | IETF | Informational draft -00 | Sep 2026 | Agent identity best practice ([IETF](https://datatracker.ietf.org/doc/html/draft-ietf-wimse-aims)) |
| Web Bot Auth | IETF | WG draft -00 | Sep 2026 | Agent recognition ([IETF](https://datatracker.ietf.org/doc/draft-ietf-webbotauth-httpsig-protocol/)) |
| Visa Trusted Agent Protocol and Intelligent Commerce | Visa | Live spec; VIC "in development and deployment" | Oct 2025 onward | Card agent tokens ([Visa](https://developer.visa.com/capabilities/visa-intelligent-commerce)) |
| Mastercard Agent Pay | Mastercard | Live in several markets; India announced 2026 | Apr 2025 onward | Agentic Tokens ([Mastercard](https://www.mastercard.com/us/en/news-and-trends/press/2025/april/mastercard-unveils-agent-pay-pioneering-agentic-payments-technology-to-power-commerce-in-the-age-of-ai.html)) |
| AP2 | Google, FIDO Alliance | v0.2; moving to FIDO | Sep 2025 onward | Signed mandates ([AP2](https://ap2-protocol.org/)) |
| Agentic Commerce Protocol | OpenAI and Stripe | Open-source beta, 2026-04-17 | Sep 2025 onward | Checkout schemas ([GitHub](https://github.com/agentic-commerce-protocol/agentic-commerce-protocol)) |
| UK commercial VRP | UKPI | Live | 2 Jun 2026 | Mandate rail ([Payment Expert](https://paymentexpert.com/2026/10/05/vrps-future-bank-to-bank-payments/)) |
| UPI Circle | NPCI | Live; Rs 15,000 per month, Rs 5,000 per transaction | 2024; BHIM Nov 2025 | Delegation chassis ([Razorpay](https://razorpay.com/blog/what-is-upi-circle/)) |
| NPCI Unified Agent Protocol | NPCI | Reported in preparation; not live | Sep 2026 reports | Agent UPI ([Indian Pay Calculator](https://www.indianpaycalculator.in/govt-news/ai-agents-upi-payments-npci-unified-agent-protocol-2026)) |
| Account Aggregator | RBI, Sahamati | Live; 252 million-plus accounts | Dec 2025 | Whole-wallet rail ([Sahamati](https://sahamati.org.in/varta-the-sahamati-newsletter-december-2025/)) |
| RBI FREE-AI | RBI | Committee report, non-binding | 13 Aug 2025 | Seven sutras ([RBI](https://www.rbi.org.in/Scripts/PublicationReportDetails.aspx?ID=1306)) |
| RBI payment data localisation | RBI | Binding | 2018 | 24-hour rule ([Treelife](https://treelife.in/?p=16560)) |
| DPDP Rules 2025 | MeitY | Notified; core obligations about 18 months | 14 Nov 2025 | Consent and grievance ([Storyboard18](https://www.storyboard18.com/digital/breaking-govt-notifies-final-dpdp-rules-sets-staggered-rollout-with-key-obligations-for-data-fiduciaries-84201.htm)) |
| SEBI IA AI rules | SEBI | In force; broader guidelines forthcoming | Jan 2025 | Adviser responsibility ([BABL AI](https://babl.ai/sebi-introduces-new-investment-adviser-regulations-addressing-ai-usage-and-transparency/)) |
| FSB Sound Practices | FSB | Consultation; final expected Oct 2026 | 10 Jun 2026 | Twelve practices ([FSB](https://www.fsb.org/uploads/P100626.pdf)) |
| MAS AI Risk Management Guidelines | MAS | Consulted; finalisation unconfirmed | 13 Nov 2025 | Inventory, materiality ([The Asian Banker](https://www.theasianbanker.com/press-releases/mas-proposes-guidelines-for-ai-risk-management)) |
| MAS SAFR | MAS and industry | White paper v1.0 and v1.1; not supervisory expectation | 3 Jul 2026 | Runtime safeguards ([MAS](https://www.mas.gov.sg/news/media-releases/2026/mas-partners-industry-to-develop-safeguards-for-ai-agents-in-finance)) |
| IMDA agentic AI framework | IMDA | Voluntary | 22 Jan 2026 | Checkpoints, agent identity ([Baker McKenzie](https://www.bakermckenzie.com/en/insight/publications/2026/01/singapore-governance-framework-for-agentic-ai-launched)) |
| FCA targeted support | FCA | In force | 6 Apr 2026 | Intermediate advice regime ([Freshfields](https://riskandcompliance.freshfields.com/post/102mmq0/fca-finalises-targeted-support-rules-applications-now-open)) |
| FCA Mills Review | FCA | Published | 6 Jul 2026 | Agentic finance ([FCA](https://www.fca.org.uk/publications/corporate-documents/mills-review)) |
| HM Treasury payments consultation | HMT | Closed; response targeted Q4 2026 | Jul to Oct 2026 | Agent consent and liability ([GOV.UK](https://www.gov.uk/government/consultations/modernising-payment-services-regulation/modernising-payment-services-regulation-consultation)) |
| PSR APP reimbursement | PSR | In force; consultation Dec 2026 | 2024 onward | 50/50 split, £85,000 cap ([Regulation Tomorrow](https://www.regulationtomorrow.com/2026/07/payment-fraud-falls-by-73m-following-psr-reimbursement-scheme/)) |
| EU AI Act (Digital Omnibus) | EU | Annex III credit from 2 Dec 2027; Art. 50 from 2 Aug 2026 | 27 Jul 2026 | High-risk and transparency ([Hunton](https://www.hunton.com/privacy-and-cybersecurity-law-blog/eu-digital-omnibus-on-ai-enters-into-force)) |
| PSD3 and PSR | EU | Compromise texts; OJ publication unconfirmed | Apr 2026 | Payments authorisation ([Mondaq](https://www.mondaq.com/ireland/financial-services/1782218/psd3-and-psr-final-compromise-texts-published)) |
| DORA CTPP oversight | ESAs | 19 providers designated | 18 Nov 2025 | Concentration ([sota.io](https://sota.io/blog/eu-ai-act-dora-ict-third-party-risk-ai-model-governance-2026)) |
| SR 26-2 | Fed, OCC, FDIC | In force; excludes generative and agentic AI | 17 Apr 2026 | US model risk ([Federal Reserve](https://www.federalreserve.gov/supervisionreg/srletters/SR2602.htm)) |
| CFPB Section 1033 | CFPB | Enjoined; reconsideration proposed | Aug 2026 | US open banking ([Consumer Finance Monitor](https://www.consumerfinancemonitor.com/2026/08/06/cfpb-sends-new-section-1033-open-banking-proposal-to-oira-for-review/)) |
| OWASP Top 10 for Agentic Applications | OWASP | Published | 9 Dec 2025 | Threat taxonomy ([OWASP](https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026)) |
| ISO/IEC 42001; 42005:2025 | ISO/IEC | Published | 2023; May 2025 | AI management and impact assessment ([CMS](https://cms.law/en/gbr/legal-updates/iso-iec-42005-2025-a-new-blueprint-for-legal-and-commercial-leaders-navigating-ai-risk-and-governance)) |
| NIST AI 600-1 | NIST | Voluntary | 26 Jul 2024 | Generative AI profile ([NIST](https://www.nist.gov/node/1855536)) |

### Appendix B: glossary

- **Accredited issuer**: an organisation admitted to the trust registry and authorised to issue Agent Passports and attestations.
- **Agent Passport**: a selective-disclosure verifiable credential binding an agent's DID to its provider, software, declared limits and verification level.
- **Attestation**: a signed statement by an accredited issuer about an agent or provider, carrying a trust mark and a status reference.
- **Autonomy level (L0 to L4)**: inform, recommend, prepare, execute with approval, execute within mandate.
- **Decision grant**: a human approval bound cryptographically to one action and consumed once.
- **Grant**: scoped, time-bound authority issued to an agent on behalf of a principal, carried as a signed token with `authorization_details`.
- **Irregularity**: anomalous agent behaviour detected by the authority service, which can trigger alerts, suspension or demotion.
- **Issuer-branded**: a consent or permissions page carrying the bank's branding as issuer of the grant.
- **Know Your Agent**: establishing an agent's identity, authority and accountability before it may act.
- **Mandate**: the customer's standing instruction and limits within which the agent may execute without per-action approval.
- **Operator override**: the platform-level halt or throttle on a provider, model, agent, workflow, connector or tool; the kill switch.
- **Principal**: the human or organisation on whose behalf an agent acts; here, the bank's customer.
- **Registry**: the trust registry of issuers, attestations, agents and trust levels; also the bank's scope registry.
- **Relying party**: a system that verifies a passport, grant or attestation before acting on it; the bank, for outside agents.
- **Scope**: a permission string of the form `resource:action[:constraint]` or `tool:<connector>:<permission>[:<tool>]`.

### Appendix C: recorded gaps and unverified items

Market: the Rodriguez post was read directly on LinkedIn (11 October 2026) and its substance is reflected in section 2; it is treated as the hypothesis under test. Revolut's own release and product page could not be fetched; AIR's model vendors and executable-action list are undisclosed. No post-launch metrics for Robinhood Agentic Trading or Lloyds' assistant. No analyst measured deposit outflows caused by consumer agents and no published CLV model links agent compatibility to retention. McKinsey's primary article on agent threats to retail banks could not be fetched. No 2026 India-specific survey on delegating money decisions to agents. Several commerce adoption figures were reached only through aggregators. Reports of regulatory scrutiny of Revolut AIR, Nubank's 2026 outcomes and the claim that 60 per cent of Erica interactions are Erica-initiated are secondary or self-reported.

Standards and rails: Mastercard developer documentation for Agent Pay could not be fetched; token formats and revocation APIs are undocumented publicly. Visa TAP carries no version or date. Grant Management's draft number is unresolved. The ACP delegate-payment schema was not read. Mastercard's live-market list conflicts between sources. No NPCI document on the Unified Agent Protocol exists; only press reports. No EBA or Commission statement on SCA for agent transactions was found. PSD3 and PSR Official Journal publication is unconfirmed.

Regulation: the full RBI FREE-AI recommendation text and the reported tolerant supervisory approach could not be verified from a reachable source. MAS final guideline status and the FSB final report are unknown. Retention periods (six years SEC 17a-4, five years SEBI) come from commentary. No court or ombudsman decision on an agent-initiated payment loss was found. No regulator sets numeric thresholds. Whether model inference is processing under RBI's 24-hour rule is unaddressed.

Platform: the capability inventory's code-level claims were consolidated from sub-inventories and not independently re-verified line by line. The two BFSI coverage documents give conflicting functional totals. Grantex's hosted `EMERGENCY_STOP_ENABLED` default was not read. The RLS behaviour of agent-tenant resolution under a non-BYPASSRLS role is a possible, unconfirmed defect. Grantex's February 2026 security audit covers SDKs at 0.1.0 and its independence could not be verified; its SOC 2 document is an internal readiness mapping, not an attestation.

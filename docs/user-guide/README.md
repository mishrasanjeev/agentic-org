# AgenticOrg User Manual

This is the maintained end-user manual. It is written for new users, workspace
administrators, process owners and integration teams, with practical BFSI
examples. It describes source-verified behavior; your deployed version,
permissions and configured providers determine what is available to you.

The UI renders these same articles at `/docs`. The landing page and application
sidebar link to that hub. Do not maintain a second independent copy in a hosted
documentation service. See the [publishing runbook](../runbooks/documentation-site.md)
for the optional `docs.agenticorg.ai` subdomain. That hostname is not yet a claim
of a configured/live service.

## Start Here

- [Choose your learning path](start-here.md)
- [Build your first useful agent](first-agent.md)
- [Workspace, companies and roles](workspace-and-roles.md)
- [Concepts and terminology](concepts.md)
- [Configure model access](model-setup.md)

## Build And Run

- [Create and configure agents](create-agents.md)
- [Run agents and chat](run-and-chat.md)
- [Knowledge, document ingestion and OCR](knowledge-and-ocr.md)
- [Connect business systems](connectors.md)
- [Create and operate workflows](workflows.md)
- [Voice, speech to text and text to speech](voice.md)
- [Browser automation and RPA](rpa.md)
- [Industry packs](industry-packs.md)

## Govern And Operate

- [Approvals and human review](approvals.md)
- [Governed business-onboarding cases](governed-cases.md)
- [Audit, monitoring and dashboards](audit-and-monitoring.md)
- [Evaluation and rollout](evaluate-and-rollout.md)
- [Security, privacy and data handling](security-and-data.md)
- [Billing and support](billing-and-support.md)
- [Troubleshooting](troubleshooting.md)
- [Team adoption checklist](adoption-checklist.md)

## Connect And Extend

- [Commerce, OACP and merchant onboarding](commerce.md)
- [API, SDK, MCP and A2A integrations](api-sdk-mcp.md)
- [Self-hosting and local learning](self-hosting.md)

## BFSI Playbooks

These are fictional reference processes, not regulatory advice, pre-approved
bank integrations or claims of autonomous financial decision-making.

- [Business onboarding / KYB investigation and human decision](bfsi-business-onboarding.md)
- [Bank and settlement reconciliation](bfsi-reconciliation.md)
- [Customer-service response preparation](bfsi-customer-service.md)
- [Insurance document and claim assistance](bfsi-insurance.md)
- [Bank merchant enablement and agentic commerce](bfsi-merchant-services.md)

## Maintenance

`index.json` defines guide titles, navigation, audience, review date and source
references. Article Markdown is the source of truth. Use a fenced `flow` block
for accessible step diagrams; each line is `Step title | Explanation`. Use
second-level headings for article sections. Internal `/docs/...` links refer to
the public reader routes; the list above uses relative links for GitHub readers.

After changing articles, run `npm --prefix ui run seo:sync` to regenerate the
tracked reader data, static HTML metadata inputs, public sitemap/LLM assets and
CSP hash references. Run UI tests and the documentation browser regression. The
build validates article slugs, source references, screenshots and related-guide
anchors. Do not infer that a runtime capability is live from a template name.

Owned by **Orchestrum Technologies LLP**. Inventor / Owner: **Sanjeev Kumar**.
Contact [sanjeev@orchestrum.in](mailto:sanjeev@orchestrum.in) or
[mishra.sanjeev@gmail.com](mailto:mishra.sanjeev@gmail.com).

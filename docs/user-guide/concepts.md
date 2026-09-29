## The simple mental model

An **agent** performs a defined job. It receives **instructions** and **inputs**, reads approved **knowledge**, and can request authorized **tools**. A **workflow** coordinates jobs. **Policies, grants and approvals** determine boundaries. **Run history and audit evidence** help you understand what actually happened.

```flow
Task and context | A person or reviewed integration supplies the request and company context.
Evidence and tools | The agent uses permitted knowledge and bounded tool access.
Review and decision | A policy or person routes uncertain or consequential work.
Recorded outcome | Inspect the result and authoritative downstream confirmation.
```

## Glossary

| Term | Plain-language meaning | Common misunderstanding |
| --- | --- | --- |
| Agent / virtual employee | A configured AI task worker | Not a person or unlimited system administrator |
| Agent type | The role or implementation used by an agent | A type name alone does not identify an authorized stored agent |
| Prompt | Versioned instructions for a model | Instructions cannot create permissions |
| Tool | A callable operation on a system | Listing a tool does not grant its use |
| Connector | Integration configuration and provider access | Registration does not prove live readiness |
| Knowledge Base | Documents and extracted content for retrieval | Uploading a file does not train or fine-tune a model |
| OCR | Turning scanned images into text | Not guaranteed perfect recognition |
| Run | One execution attempt and its recorded result | A prepared answer is not an external transaction |
| Shadow | A candidate/evaluation state | Not proof that every side effect is automatically blocked |
| HITL | Human-in-the-loop review | A confidence percentage is not business authorization |
| Workflow | Reviewed steps, branches and handoffs | A schedule does not bypass an approval |
| Tenant | Your organization's isolation boundary | Not a company name in a prompt |
| Company context | The authorized business entity for a task | Not a way to cross tenant boundaries |
| Grant / scope | Bounded permission for an identity or tool | Not permission for unrelated financial decisions |
| Governed case | Evidence investigation, policy and human decision record | Separate from a generic agent run |
| Policy tier | Deterministic risk classification in a governed case | Not an LLM confidence score |
| Artifact | Source-linked, bounded evidence or capability metadata | Not transaction authority by itself |
| TTL / freshness | How long evidence may be reused and how current it is | A valid signature does not make old inventory current |
| OACP | Agentic commerce trust and interoperability work | Not a claim of adopted public standardization |
| MCP / A2A | Integration and discovery surfaces for agents/tools | Not automatic inclusion in every AI marketplace |

## Who owns what in commerce

AgenticOrg runs the buyer and seller agents. Grantex supplies trust, policy and canonical artifact authority. The merchant system remains the source of catalog, inventory and order truth. The provider, bank or fintech owns payment and mandate execution. POS or merchant systems confirm their own fulfillment outcomes.

A valid cached artifact can support non-binding conversation without sending every message through Grantex. An agent cannot use that cache to invent a price guarantee, inventory hold, successful payment or completed order. See [Commerce](/docs/commerce).

## Know the outcome vocabulary

Read the actual state: draft, prepared, queued, running, awaiting review, failed, refused or completed are not interchangeable. For integrations, require the provider/system-of-record confirmation before calling a business action successful. If you cannot explain the evidence behind a result to a colleague, pause and investigate rather than promoting it.

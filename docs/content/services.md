# Content services: drafting, summarisation and extraction

With `AGENTICORG_CONTENT_SERVICES_ENABLED` on, three reusable capability APIs are available under
`/content` (`core/content/`). Each service is an API with an input schema, an output schema, a
guardrail profile and an evaluation dataset; `GET /content/services` lists them with all four.

## How a service runs

`core/content/services.py` runs every service the same way:

1. Every text field of the input passes the tenant's **input guardrails** (`core/governance/guardrails`,
   use case `content.<service>`). A blocked input is refused (`guardrail_blocked`). A masked, redacted or
   tokenised input is rebuilt field by field from the transformed text and that is what the model sees
   and what a kept draft records; a transform that cannot be mapped back onto the fields is refused
   (`guardrail_transform_unmappable`).
2. Knowledge-base sources pass the **retrieval guardrails** before they enter the prompt: a withheld
   document is left out (and counted under `guardrails.retrieval`), a transformed one is used
   transformed, and a request whose every source is withheld is refused (`sources_withheld`).
3. With pre-model pseudonymisation on (`pseudonymisation.pre_model`) the prompt is pseudonymised before
   it leaves and the answer restored before it is checked; a setting or map that cannot be read is
   refused (`pseudonymisation_unavailable`), never sent raw.
4. The model answers in JSON through the direct router (`AGENTICORG_CONTENT_SERVICES_MODEL`, else the
   router's default). An answer that is not valid JSON or does not match the service's output schema
   is sent back once with the problems named; a second failure is refused (`model_output_invalid`).
5. The service checks the answer against its sources: only given sources count as used, quotes must
   be in their source, citations must name given documents.
6. The whole structured output, every field, passes the **output guardrails**; for the grounded
   services the sources travel as context so a grounding detector can judge the answer against them.
   A transformed output is returned transformed in every field; a blocked one is refused.

Sources are inline texts (`sources`/`documents`, each with an id) or approved knowledge-base
documents by id (`knowledge_document_ids`) under the caller's document access
(`core/content/sources.py`).

## The services

**Governed drafting** (`POST /content/draft`, `core/content/drafting.py`): a notice, circular,
letter, email, memo or FAQ from a subject, points, audience, tone and constraints. The draft names
the sources it relied on (`sources_used`, only ones it was given), lists the placeholders it wrote
for facts it did not have (`[DATE]`, `[BRANCH NAME]`), and is kept in the drafts queue
(`content_drafts`, migration `v6z62_content_drafts`). Policy: a notice or a circular, or any draft
the caller marks with `require_approval`, is `pending_approval` until a second person approves it
(`POST /content/drafts/{id}/decide`, tenant administrator, never the author); other drafts are
kept as `draft`. Drafting and decisions need an active human administrator of the tenant (an API key
or agent credential is refused), so the author and the checker are two identified people; a draft
with no recorded author cannot be decided. `GET /content/drafts` and `GET /content/drafts/{id}` read the queue.

**Structured summarisation** (`POST /content/summarise`, `core/content/summarisation.py`): a
summary across up to ten documents at a chosen length, key points that each cite their documents
(a point citing none is kept but counted as ungrounded), per-document notes, open questions, and the
documents the summary did not cover.

**Obligation and deadline extraction** (`POST /content/extract`, `core/content/extraction.py`):
who must do what by when. Every item quotes the text it comes from; an item is dropped and counted
(`dropped`) unless its quote is a meaningful span (at least 12 characters and three words), appears in
its source, and supports the obligation (at least half of the obligation's content words are in it). Deadlines are ISO dates or null with the basis the
document gives; items are sorted by deadline.

## Evaluation datasets

Each service ships synthetic evaluation cases (an input and what the answer must contain or avoid).
`POST /content/services/{name}/dataset` (tenant administrator) creates the dataset for the tenant through the
evaluation framework (`core/evals/datasets.py`), so the service can be scored and gated like a
prompt; a dataset that already exists is reported, not duplicated.

Access: the read routes need the `audit:read` scope and every other call `approvals:write`
(`api/route_enforcement.py`), as well as any administrator requirement above.

Off, the catalogue still answers with `enabled: false`; every other route is not found.

# Content services: drafting, summarisation and extraction

With `AGENTICORG_CONTENT_SERVICES_ENABLED` on, three reusable capability APIs are available under
`/content` (`core/content/`). Each service is an API with an input schema, an output schema, a
guardrail profile and an evaluation dataset; `GET /content/services` lists them with all four.

## How a service runs

`core/content/services.py` runs every service the same way:

1. The input's text passes the tenant's **input guardrails** (`core/governance/guardrails`, use case
   `content.<service>`); a blocked input is refused (`guardrail_blocked`), never quietly changed.
2. The model answers in JSON through the direct router (`AGENTICORG_CONTENT_SERVICES_MODEL`, else the
   router's default). An answer that is not valid JSON or does not match the service's output schema
   is sent back once with the problems named; a second failure is refused (`model_output_invalid`).
3. The service checks the answer against its sources: only given sources count as used, quotes must
   be in their source, citations must name given documents.
4. The rendered output passes the **output guardrails**; for the grounded services the sources travel
   as context so a grounding detector can judge the answer against them. A transformed output is
   returned transformed; a blocked one is refused.

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
kept as `draft`. `GET /content/drafts` and `GET /content/drafts/{id}` read the queue.

**Structured summarisation** (`POST /content/summarise`, `core/content/summarisation.py`): a
summary across up to ten documents at a chosen length, key points that each cite their documents
(a point citing none is kept but counted as ungrounded), per-document notes, open questions, and the
documents the summary did not cover.

**Obligation and deadline extraction** (`POST /content/extract`, `core/content/extraction.py`):
who must do what by when. Every item quotes the text it comes from; an item whose quote is not in
its source is dropped and counted (`dropped`). Deadlines are ISO dates or null with the basis the
document gives; items are sorted by deadline.

**Narrative to payload** (`POST /content/structure`, `core/content/structuring.py`): a schema-shaped
JSON object from free text. The schema is given inline (`schema`), named from the tenant's schema
registry (`schema_name`, the tenant's own row first, then a global one) or named from the built-in
domain schemas. The payload is validated against it and the result says what failed; with `strict`
an invalid payload is refused. `format: xml` or `both` adds a deterministic XML rendering (keys
become elements, list items the singular of their key, nulls left out) that is parsed back to prove
it is well formed. What the text says but the schema cannot hold is listed as `unplaced`.

**Policy-grounded response** (`POST /content/respond`, `core/content/responding.py`): an answer to
a question written only from an approved source set (inline or knowledge-base documents). Every
claim carries a citation whose quote must be in its source; with no valid citation the response is
withheld and the service says the approved sources do not cover the question, naming the gaps.

**Audience-adaptive tone** (`POST /content/adapt`, `core/content/tone.py`): the same facts
rewritten for an audience (customer, relationship manager, internal, regulator, vulnerable
customer, partner), a tone and a reading level. Every number, amount, date and percentage of the
original, and every term in `keep`, must still be there: `facts_preserved` and `missing_facts`
say so.

**Clause assembly** (`POST /content/assemble`, `core/content/clauses.py`): a document built from
the approved clause library by rules, with no model. A clause (`/content/clauses`, tenant
administrator) belongs to document types, sits in a category with an order, applies when every
one of its conditions holds for the facts given (`equals`, `in`, `gte`, `exists`, ... on dotted
fields), and carries `{placeholders}` filled from the facts. A change makes a new version that
waits for approval again; approval is by a second person; a retired clause no longer assembles.
The result names the clauses and versions used, the clauses skipped, the facts missing (left
visible as `[PLACEHOLDER]`), the required clauses whose conditions failed, and whether the
document is complete.

**Document translation** (`POST /content/translate`, `POST /content/translate/batch`,
`core/content/translation.py`): a translation into any of the supported Indian languages or
English (`GET /content/languages`), formal or neutral register, plain text or Markdown, with a
glossary (term to translation) and terms to keep verbatim (product names, identifiers). The model
translates; the service checks deterministically that every number, amount, date and percentage of
the source is still there, with its currency and magnitude (`₹5 lakh`, `₹5 crore` and a bare `5`
are different figures; native digits count), that each glossary term present in the source has its translation in the
output, that the verbatim terms were kept, and that at least half the letters are in the target
script; `trusted` is true only when all four hold, and the `checks` say which did not. When an
output guardrail changes the translation after the checks, `trusted` is false and
`checks.output_transformed` is true. A text is at most 4,000 characters, so the translation fits
the completion budget. `verify`
adds a second model call that translates back to the source language and the word overlap with
the source, so a reviewer sees how much came through. A batch of up to twenty texts is translated
one by one with the same settings; an item's failure is reported in place.

## Evaluation datasets

Each service ships synthetic evaluation cases (an input and what the answer must contain or avoid).
`POST /content/services/{name}/dataset` creates the dataset for the tenant through the
evaluation framework (`core/evals/datasets.py`), so the service can be scored and gated like a
prompt; a dataset that already exists is reported, not duplicated.

Off, the catalogue still answers with `enabled: false`; every other route is not found.

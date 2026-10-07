# Provenance and lineage

Behind `lineage_enabled` (default off; `AGENTICORG_LINEAGE_ENABLED`). Off, `GET /lineage/status`
answers `enabled: false` and every other lineage route is not found; nothing else changes.

## The model

A **node** is one thing the platform keeps or uses, named by its kind and a stable reference:
a `source` (a URL, a connector object, an upload), a `document`, a `chunk`, an `embedding`, a
transaction `record`, a `transcript`, a `finding`, a `draft`, or a `model_use`. A node carries its
origin (`source`), a `version` (a content hash such as `sha256:…`, or the version the source
gave), when it was observed and bounded attributes. A node is kept once under
(kind, reference, version), so a re-ingested document with the same bytes is the same node and a
changed one is a new version beside it.

A **step** joins two nodes with what was done between them: `acquire`, `extract`, `chunk`,
`embed`, `ingest`, `transcribe`, `summarise`, `detect`, `draft`, `retrieve` or `generate`; the
tool that did it, a hash of its parameters and when. A step is kept once under
(from, to, step). Both tables are tenant scoped under forced row-level security
(`core/models/lineage.py`, migration `v6z75`).

## What records provenance

- Knowledge ingestion (`core/rag/ingest.py`) notes its chain after the rows are committed: the
  source and the document (versioned by the hash of the uploaded bytes, joined by `extract` with
  the extraction method), every chunk (versioned by its content key, joined by `chunk`) and every
  chunk's embedding (versioned by the embedding model, joined by `embed`). Ingestion never fails
  on lineage: a failure is logged and the ingestion result is unchanged.
- Transaction ingestion (`core/txn/records.py`) notes each kept record as acquired from its
  source, versioned by the record's content.
- `POST /lineage` notes a chain the platform did not produce itself (a connector sync, a feed):
  the nodes, then the steps between them by position; a long chain is sent in parts, since the
  keys make it idempotent. The attributes may carry the lawful basis and the licence of an
  acquisition, which the trace then shows.

## Reading it

`GET /lineage/nodes/{kind}/{ref}?version=` describes one thing: its versions newest first, its
sources (the source nodes it traces to, or the origin it declared), the processing history back
to them in order, whether the trace reached a source (`complete`), and whether it was cut by the
bounds. `GET /lineage/trace/{kind}/{ref}?direction=upstream|downstream|both&hops=&version=` walks
the graph from the newest version (or the one named) and returns the nodes and the steps, bounded
by eight hops and five hundred nodes, saying when it was cut.

References that carry slashes or a fragment (`upload://invoice.pdf#chunk3-ab12`) are sent
URL-encoded in the path.

## Access

Reads need `audit:read`, writes `approvals:write` (`api/route_enforcement.py`, family `lineage`).
Telemetry carries counts only.

## Incremental synchronisation

A **sync source** (`core/lineage/sync.py`, tables `lineage_sync_sources` and `lineage_sync_runs`,
migration `v6z76`) names a feed the tenant administrator set up: a public HTTPS endpoint
(validated for egress, DNS pinned) that answers `{"items": [...], "cursor": "..."}` for
`GET <url>?since=<cursor>`, with an optional bearer token kept encrypted for the tenant and never
returned. Each item carries a stable `ref`, a `kind` (`document` with `title`, `mime_type` and
`text` or `content_base64`; or `record` with the transaction `record`), a `version` (or one is
taken from the content) and `modified_at`.

A **run** (`POST /lineage/sync/sources/{id}/run`, or the schedule) fetches the items since the
cursor, skips every item whose (kind, reference, version) provenance already keeps, ingests the
rest (documents through knowledge ingestion, records through the transaction store, both of which
note their lineage), links each document to the feed it was acquired from with an `acquire` step
that carries the source's `basis` and `licence` when its config names them, and records what it
received, processed, skipped and failed with the first errors. The cursor advances only when
nothing failed, so a failed item is offered again; the run is `completed`, `partial` or `failed`.

`GET/POST /lineage/sync/sources`, `PATCH/DELETE /lineage/sync/sources/{id}` (the cursor can be
reset), `GET /lineage/sync/sources/{id}/runs`. The **schedule**: each source has an interval
(five minutes to a week); a sweep claims the due sources of a tenant under a row lock and runs
them in turn, so two sweepers never run the same source. The sweep runs from Celery beat every
five minutes (`core/tasks/lineage_tasks.py`) and is a no-op unless `lineage_sync_sweep_enabled`
is on as well. Another way of listing
changed items (a connector) registers a fetcher under its own source kind.

## Next

The lineage graph in the console is the last part of this work package.

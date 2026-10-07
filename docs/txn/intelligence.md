# Transaction intelligence: aggregation and detectors

With `AGENTICORG_TRANSACTION_INTELLIGENCE_ENABLED` on, movements on accounts are kept, aggregated by
entity and run through detectors whose findings a person dispositions (`core/txn/`). Off,
`GET /txn/status` answers `enabled: false` and the rest is not found.

## Records

A record is one movement on one account: a reference that makes it idempotent (one is derived when
none is given), the account, the customer where known, the counterparty, the direction and amount,
the channel (cash, transfer, upi, cheque, card, atm, other; read from the description when not
named), the branch, when it was booked and a description. `POST /txn/records` takes a batch of up
to 500; a record already kept under its reference is skipped. `POST /txn/import/document/{id}`
books the line items of a bank statement kept by document processing as records on the
statement's account, with the statement's checks beside each (`core/txn/records.py`,
`txn_records`, tenant scoped under a forced row-level policy with a leading index on every lookup).

## Entities

`GET /txn/entities` lists the accounts, customers and counterparties seen in the recent records
with how much moved through each; `GET /txn/entities/{kind}/{ref}` is one entity's view: what came
in and went out, the counts, the split by channel and by branch, the counterparties that moved the
most, the days as a series, the share of cash, the accounts and customers involved, and the findings
raised on the entity (`core/txn/aggregate.py`).

## Detectors

`core/txn/detectors.py` is deterministic over the records, so a finding is the same on every run
and every row behind it is named.

- Structuring: cash deposits each under the reporting threshold, at least the minimum number of
  them within the window on one account, together at or above the threshold. Spread across branches
  or twice the threshold, the finding is high; otherwise medium.
- Pass-through: an inflow at or above the minimum followed within the window by outflows that take
  at least the ratio of it away. At or above 95 per cent within a day, high; otherwise medium.

The thresholds come from the business console (`txn.structuring_threshold`,
`txn.structuring_window_days`, `txn.structuring_min_count`, `txn.passthrough_window_hours`,
`txn.passthrough_ratio`, `txn.passthrough_min_amount`), shown only while transaction intelligence is
on, with the catalogue's defaults where unset.

## Fund-flow graphs

`GET /txn/graph/{kind}/{ref}?hops=&min_amount=&since_days=` builds the graph around an entity
(`core/txn/graph.py`): every record booked against it makes its counterparty a node and each
direction of flow between two nodes an edge carrying the total, the count, the first and last
movement and the channels; a node found at one hop is expanded at the next from its own records, up
to four hops and two hundred nodes, so the graph never runs away. The answer lists the nodes by hop
with what came in and went out and the findings on each, the edges by amount, and the heaviest
outward paths of money from the root, ranked by the amount carried. A node known only by name (a
statement import) expands by that name; a customer with more accounts than the node bound is capped
and the answer says so. `GET /txn/graph/{kind}/{ref}/export?format=json|csv` carries the graph,
the records behind every edge and the findings, as JSON or as CSV (the records, then the findings
with their severity, status and disposition), for the case file: every record behind an edge is
fetched in batches and the answer says how many were expected and how many were missing, and a
cell a spreadsheet would read as a formula is kept as text. The Transactions page draws the graph
by hop, wide enough for the last hop, expands a node on click, lists the paths and the findings on
the entity, offers the disposition controls to the approval roles only, and downloads the export
through the API client. The navigation entry appears only once the subsystem reports itself on.

## Narratives and evidence

`POST /txn/findings/{id}/narrative?method=` drafts the suspicious-transaction narrative of a finding
(`core/txn/narrative.py`): what was seen, over which period, on which accounts, through which
counterparties, why the detector raised it, a timeline of the supporting movements, the parties, the
basis, a recommendation (dismiss, confirm, escalate) and the gaps an investigator should still check.
The model path goes through the content services' checked JSON call; the extractive path writes the
same sections from the facts, so a draft exists without a model; `auto` takes the model and falls
back to the facts, saying so. The draft is kept on the finding, which stays open in the investigator
queue for a person to review; nothing is filed. In the queue a finding is listed only for the
roles the Transactions tab admits (admin, COO, auditor, CFO), whatever another workbench opens;
it is decided by a signed-in person, and rejecting it needs a reason in the notes. `GET /txn/findings/{id}/evidence?format=json|csv`
is the evidence package: the finding with its narrative, the supporting rows, the entity view, the
fund-flow graph and rows, exported with a digest over the whole so a reviewer can tell it was not
altered; the digest is recorded on the finding. Open findings appear in the workbench review queue as
the `finding` kind (the investigator's Transactions tab): approve confirms, reject dismisses with a
reason, and escalation is taken on the Transactions page with the case reference.

## Findings and disposition

`POST /txn/detect` runs the detectors over the recent records (one account or all, `since_days`)
and keeps every new finding under its fingerprint (kind, entity, the rows behind it), so running
again never raises the same one twice (`core/txn/findings.py`, `txn_findings`). A finding stays
open until a person dispositions it through `POST /txn/findings/{id}/disposition`: dismissed with a
reason, confirmed, or escalated with the reference of the governed case opened for it; the detectors
file and close nothing. `GET /txn/findings` lists by status, kind or entity;
`GET /txn/findings/{id}` returns the finding with the rows that support it. A disposition is a
person's decision: an API key or an agent token holding the write scope is refused. A derived record
reference carries the source and the row, so two identical lines of one statement are two
movements, and an import books every row in batches. The transaction routes
map onto enforced RBAC scopes: a read needs `audit:read`, a write `approvals:write`.

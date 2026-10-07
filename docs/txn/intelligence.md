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

## Findings and disposition

`POST /txn/detect` runs the detectors over the recent records (one account or all, `since_days`)
and keeps every new finding under its fingerprint (kind, entity, the rows behind it), so running
again never raises the same one twice (`core/txn/findings.py`, `txn_findings`). A finding stays
open until a person dispositions it through `POST /txn/findings/{id}/disposition`: dismissed with a
reason, confirmed, or escalated with the reference of the governed case opened for it; the detectors
file and close nothing. `GET /txn/findings` lists by status, kind or entity;
`GET /txn/findings/{id}` returns the finding with the rows that support it. The transaction routes
map onto enforced RBAC scopes: a read needs `audit:read`, a write `approvals:write`.

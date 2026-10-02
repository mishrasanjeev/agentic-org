# Tamper-evident audit: the hash chain and the model call digests

> **Status: wired, off by default.** The chain columns, the sealing and verification tasks,
> the endpoints and the evidence section are in place and tested. No tenant's rows are sealed
> until a deployment sets `AGENTICORG_AUDIT_CHAIN_ENABLED=true`, and no environment of this
> platform has done that yet. See [What is not here yet](#what-is-not-here-yet).

Every audit row the platform writes is signed on its own with the audit key
(`core/tool_gateway/audit_logger.py`): the signature covers the row's fields, so an edited row
no longer verifies. A signature proves the platform wrote the row. It does not prove the trail is
whole: a row removed, inserted or reordered leaves every remaining signature valid. The chain
closes that gap.

## The chain

Each tenant has one chain. The sealing task (`core.tasks.audit_chain_tasks.seal_audit_chains`,
every five minutes) takes the tenant's unsealed rows in write order and gives each one:

| Column | Meaning |
| --- | --- |
| `chain_seq` | the row's position, 1 upward, unique per tenant |
| `chain_prev` | the link hash of the previous row (`0…0` for the first) |
| `chain_hash` | SHA-256 over the chain version, `chain_prev`, the row's signed payload and its signature |
| `sealed_at` | when the row was linked |

The newest link is the chain head. After every sealing the task logs `audit_chain_sealed` with
the head's sequence number and hash; a log retention outside the platform therefore holds an
anchor that a later rewrite of the table cannot change.

Sealing runs under the tenant's own row-level security context and locks the head row and the
batch it links, so two sealers never fork a chain. A batch is at most
`AGENTICORG_AUDIT_CHAIN_SEAL_BATCH` (5000) rows and a run links at most twenty batches per tenant,
so a backlog drains over a few runs without one run holding locks for long. Deleted tenants are
included: their rows still exist and still seal.

## Verification

`verify` walks the sealed rows in sequence order and recomputes every link. For each row it
checks, in this order, that:

1. the sequence number is the one expected (`sequence_gap` otherwise: a row was removed);
2. `chain_prev` equals the previous row's link hash (`previous_link`: a row was inserted,
   reordered or re-sealed);
3. `chain_hash` equals the link recomputed from the row's current fields (`link_hash`: the row
   was edited);
4. the row's own signature still matches its fields (`signature`).

The first break stops the walk and is reported with its sequence number, row id and reason. A
row sealed without a signature (written before signing existed) is counted as `unsigned`, not
a break. The daily task `verify_audit_chains` verifies every tenant end to end, logs
`audit_chain_broken` for a broken one and counts results in
`agenticorg_audit_chain_verifications_total{result}`.

The endpoints (an audit reader's scope):

| Endpoint | Returns |
| --- | --- |
| `GET /api/v1/audit/chain` | whether sealing is on, the head (sequence, hash, sealed at) and how many rows wait for sealing |
| `GET /api/v1/audit/chain/verify?from_seq=&limit=` | a verification from `from_seq` over at most `limit` rows (default 10,000): the status (`empty`, `verified`, `broken`), the range checked, the verified and unsigned counts and the first break |

The compliance evidence package's `audit_logs` section carries `chain`: the head, the backlog and
a verification of the newest thousand links, with any unreadable part reported rather than
raised.

### Reading a break

A break names the first row at which the chain stops holding. Everything before it verified;
nothing after it is trusted until the cause is known. `link_hash` at one sequence number means
that row's fields differ from what was sealed. `sequence_gap` means the row with the missing
number is gone. `previous_link` means the rows around that number are not in the order they
were sealed in. The anchor in the log retention says which head the platform had at each
sealing, so the last trustworthy head is recoverable even when the table is not.

## Model request and response digests

Routing records (`docs/governance/model-gateway.md`, written while the gateway and records are
on) now carry three digests, each a SHA-256 over canonical JSON:

| Field | Over |
| --- | --- |
| `prompt_digest` | the system prompt the model was given (the prompt's version, in effect) |
| `request_digest` | every message sent to the model, in order, after pseudonymisation and the guardrail input stage: what the model saw |
| `response_digest` | the model's answer |

The digests are part of the record's signature, so a record cannot be re-pointed at other
content, and the content itself is never stored: a party that holds the prompt, the messages
or the answer can show they match the record, and nobody can learn them from it. Records
written before this change verify unchanged (their signatures covered no digests, and the
digest fields are signed only when present).

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `AGENTICORG_AUDIT_CHAIN_ENABLED` | `false` | Run the sealing task. Verification and the status read whatever is sealed either way. |
| `AGENTICORG_AUDIT_CHAIN_SEAL_BATCH` | `5000` | Rows linked per batch. |

## Tests

`tests/unit/governance/test_audit_chain.py` covers the link hash, sealing from the genesis
value and from an existing head, verification of an intact chain, the detection of an edited
row, a removed row and reordered rows with the right reason and sequence number, verification
from a later sequence number and under a limit, the sealing task off by default and isolating a
failing tenant, the verification task's report and the evidence section with unreadable parts
reported. `tests/unit/governance/test_audit_chain_api.py` covers the two endpoints and their
scope. The digests are covered in `tests/unit/governance/test_model_gateway_records.py`.

## What is not here yet

- **No external anchor service.** The head is anchored by the log retention; publishing it to
  an external timestamping service is not wired.
- **No write-once storage.** The table is protected by the chain and the signatures, not by a
  storage layer that refuses rewrites.
- **No console view** of the chain state; the evidence package and the endpoints carry it.

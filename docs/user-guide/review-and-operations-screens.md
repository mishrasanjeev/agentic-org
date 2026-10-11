## Six screens that ship switched off

Six console screens belong to services that are built into the product but switched off in a default installation. Each needs its own deployment setting, which a platform operator turns on. While a service is off, it processes nothing and its other routes answer as not found.

| Screen | Sidebar entry and who sees it | Deployment setting (off by default) | While it is off |
| --- | --- | --- | --- |
| Documents | Always listed for roles that can open the approval queue | `AGENTICORG_IDP_ENABLED` | The page shows "Document processing is off for this deployment"; the API answers `404` `idp_disabled` |
| Conversations | Always listed for administrators | `AGENTICORG_CONVERSATION_V2_ENABLED` | The page shows "Conversational services are off for this deployment"; the API answers `404` `conversation_disabled` |
| Workbenches | Always listed for every role except merchant | `AGENTICORG_WORKBENCH_V2_ENABLED` | The page says "Workbenches are off for this deployment." |
| Calls | Administrator, COO and auditor; hidden until the service is on | `AGENTICORG_SPEECH_INTELLIGENCE_ENABLED` | No sidebar entry |
| Transactions | Administrator, CFO, COO and auditor; hidden until the service is on | `AGENTICORG_TRANSACTION_INTELLIGENCE_ENABLED` | No sidebar entry |
| Lineage | Administrator, CxO roles, auditor and domain lead; hidden until the service is on | `AGENTICORG_LINEAGE_ENABLED` | No sidebar entry |

An "off for this deployment" message is configuration, not an empty queue. Ask the platform owner whether the service is meant to be on; do not treat it as "nothing to review". The sidebar only offers a page; the server still checks each request against your role and scopes.

```flow
Choose one service | Pick the screen a pilot needs and name its business owner and reviewer.
Agree the data terms | Confirm what the service keeps, who may read it and how it is removed.
Turn on the setting | The platform operator sets the service's deployment setting.
Confirm the screen | Reopen the screen and check that the off message has gone.
Test with fictional material | Run one synthetic document, call or record through the service.
Review before relying on it | A named person checks each output before anyone acts on it.
```

## Documents: review extracted fields

With document processing on, a file sent to `POST /api/v1/idp/analyse?store=true` is split into typed documents (bank statement, salary slip, invoice, identity document and other types in the catalogue), and its fields and tables are extracted with the place on the page each came from. The screen has no upload button; files arrive through that API.

Filter the list by **Needs review**, **Processed**, **Approved**, **Rejected** or **All**. Open a file to see each page with a box drawn around every field. The box colour shows whether a field was read with high or low confidence, is missing, or has been corrected. Click a field to jump to its page, type a correction and choose **Save**; the extracted value stays visible beside your correction. Then **Approve** or **Reject** the whole file. The review reasons at the top say why a file was routed to a person, for example a missing required field or a page whose text could not be read.

A confidence colour is a hint, not a verified value. Check account numbers, amounts, dates and identity numbers against the page image every time.

## Conversations: watch and take over a chat

With conversational services on, an administrator sees every chat conversation in progress or escalated, newest activity first, with who holds each one. Open one to read its transcript. **Take over** stops the runtime from answering: the user's next messages are shown to you and your replies reach the user's chat. **Release** hands the conversation back to the runtime. The screen updates through the live feed, which carries only the fact that something changed, never the message text.

The same setting turns on banking intents in chat and passes earlier turns to the agent as context; see [Run agents and use chat](/docs/run-and-chat).

## Workbenches: one place per role

With workbenches on, the index lists the workbenches your role holds or an administrator assigned to you. A workbench shows tabs with the number of items waiting behind each. Most tabs open the page they name (for example Documents or Approvals), and that page applies its own permissions. A content-drafts tab lists drafts waiting for a decision with **Approve** and **Reject**. Use **Refresh counts** before reporting a queue size; a dash means the count is not available, not zero.

## Calls: recordings, transcripts and summaries

With speech intelligence on, a recording sent to `POST /api/v1/speech/recordings` is split by speaker and transcribed, by a local engine where the deployment installed one or through the tenant's own speech-provider credential. The audio and the transcript are kept encrypted under the tenant's key. The screen has no upload button.

Open a recording to see each speaker's talk time and turns. **Summarise** asks a model for the intent, key points and next actions; **From the words only** builds the same summary from the transcript without a model. **Redact spoken data** finds spoken card numbers, one-time codes, CVVs and PINs, replaces them in the transcript, silences them in the audio and records only the kinds and times removed. Sentiment, empathy, talk ratio and escalation risk are review aids for a supervisor, not a judgement of the staff member or the customer. Agree retention and removal of recordings with the data owner before turning the service on; see [Security and data](/docs/security-and-data).

## Transactions: findings under human disposition

With transaction intelligence on, transaction records are loaded through the API and detectors look for patterns such as structuring and pass-through movements. Each finding names an entity, a summary, a severity and its supporting records. Filter by **Open**, **Confirmed**, **Escalated** or **Dismissed**.

**Draft narrative** writes a draft account of the finding, by a model or from the facts alone, with what is still to check. **Download evidence** exports the finding with its supporting rows and a digest over the package. **Show the fund flow** draws the accounts and counterparties around the entity across a chosen number of hops. A reviewer with approval access records **Confirm**, **Escalate** or **Dismiss**; a dismissal needs notes. A narrative is a draft for an investigator, never a regulatory filing, and auditors can read findings without deciding them.

## Lineage: where a kept item came from

With lineage on, ingestion records each kept item's source, version and processing steps. Search by kind and reference, choose **Upstream (sources)**, **Downstream (uses)** or **Both** and a number of hops, then open an item to trace it. The sync-sources table lists scheduled sources with their last run, and **Run now** starts one. Items ingested while lineage was off have no recorded chain, so "No provenance kept yet" can mean the item predates the switch.

Next: [Approvals](/docs/approvals), [Security and data](/docs/security-and-data), [Troubleshooting](/docs/troubleshooting), [Self-hosting](/docs/self-hosting).

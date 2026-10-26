# Merchant onboarding desk: applications

An application is where a lead becomes a merchant. Each lead has one application, and every channel works on the same one: a merchant can start answering by text message, carry on from the web page, and never repeat an answer. The application asks only for what the partner's policy lists for that merchant type and says why each item is needed. When every required item is in, the applicant submits it. A complete application maps to the `business_case` application a governed case is opened with.

Applications are part of the onboarding desk and are off with it. With `AGENTICORG_ONBOARDING_DESK_ENABLED` unset, every route on this page is not found.

## What an application asks for

| Item | Kind | Default |
|---|---|---|
| `legal_name` | Legal business name | Required, always |
| `business_structure` | Corporation, LLC, partnership, sole proprietorship, non-profit, cooperative or other | Required |
| `registration_number` | State file number, corporation number, or NEQ in Quebec | Required |
| `tax_id` | EIN in the United States, Business Number in Canada | Required |
| `registered_address` | Street, city, state or province, and postal code | Required |
| `activity` | What the business does, from a fixed list | Required |
| `owners` | Owners and people in control: name, person or business, role, optional ownership and date of birth | Required, always |
| `formation_document` | Articles, or certificate of formation or incorporation | Required |
| `trading_name` | Name customers know the business by | Optional |
| `website` | The business's website | Optional |
| `tax_id_document` | IRS EIN letter or CRA Business Number confirmation | Optional |

Every item has a label and a reason in English and French. The applicant sees them in the lead's language, French by default in Quebec.

**Answer checks.** The checks are the same on the web and by text:
- an EIN has 9 digits, and a Business Number has 9 digits with an optional program account such as `RT0001`;
- US ZIP codes and Canadian postal codes are checked and normalised;
- states and provinces are two-letter codes;
- ownership is between 0 and 100% and may not add up to more than 100%;
- a date of birth may be just a year or a year and month.

**What is never asked.** The desk never asks for a personal Social Security or Social Insurance Number.

### The partner's policy

`PUT /onboarding-desk/application-policy` sets the required and optional items by default and per merchant type. It needs a tenant administrator.

```json
{
  "default": {
    "required": ["legal_name", "business_structure", "tax_id", "registered_address", "owners", "formation_document"],
    "optional": ["website"]
  },
  "merchant_types": {
    "food_truck": {"required": ["legal_name", "tax_id", "owners"], "optional": ["website"]}
  }
}
```

Rules for the policy:
- `legal_name` and `owners` are always required.
- An item cannot be both required and optional.
- At most 50 merchant types.
- An answer to an item the policy does not list for that application is refused with `item_not_requested` and never stored.

`GET /onboarding-desk/application-policy` returns the policy and the full catalogue of items.

## The applicant's side

The onboarding link in every outreach message carries a signed token, valid for 14 days.

1. **The page opens a session.** The partner's page posts the token to `POST /api/v1/onboarding-desk/apply/session` and gets:
   - a two-hour session;
   - the partner name;
   - the application, created on first use.
2. **Later calls carry the session.** They send it in the `X-Applicant-Session` header, so the link itself stays out of later requests. An onboarding link, unsubscribe link or expired session is refused with 401.
3. **A closed lead stays closed.** A lead that opted out or was closed gets 410.

| Call | Does |
|---|---|
| `GET /apply/application` | The application as it stands: items with labels, reasons and answers, documents, what is missing, whether it is ready |
| `PUT /apply/answers` | Saves up to 20 answers; `null` clears one. Each refused answer comes back with a code and a message in the applicant's language |
| `POST /apply/documents` | Uploads a document for a document item (form fields `item` and `file`) |
| `POST /apply/documents/{id}/confirm` | Saves the values the document suggested that the applicant confirms |
| `DELETE /apply/documents/{id}` | Removes one of the applicant's own uploads |
| `POST /apply/person` | Asks for a person, with an optional message |
| `POST /apply/submit` | Submits a complete application; refused with `application_incomplete` and the missing items otherwise |

Once submitted, the application is closed to changes and the lead stops getting reminders.

## Documents

**What is accepted.** PDF, PNG and JPEG files, up to 10 MB and 10 pages, with at most 10 documents per application. The type is recognised from the file's first bytes, not its name or declared type. The stored name keeps no path and ends in the real extension.

**Reading.** The desk reads the text with the document pipeline: a PDF's text layer, or OCR where the engine is installed. It looks for:
- the legal name;
- the EIN or Business Number;
- the registration number.

Each value found goes through the same checks as a typed answer, and becomes a suggestion only if it passes.

**Confirming.** Suggestions are shown to the applicant and saved only when the applicant confirms them. The answer then records the document as its source.

**Result of a read.** Each document reports one of:

| Result | Meaning |
|---|---|
| `suggested` | Values were found |
| `nothing_found` | The text was read but held nothing usable |
| `ocr_unavailable` | A scanned page and no OCR engine |
| `unreadable` | A damaged file, or more than 30 seconds to read |

Document text never reaches a model.

## Talking to a person

`POST /apply/person`, or the reply PERSON (PERSONNE in French) by text, opens a hand-off. An application has at most one open hand-off, and asking again changes nothing. The request is added to the application's transcript.

`GET /onboarding-desk/handoffs` lists open requests with the applicant's message. The transcript is on the application. `POST /onboarding-desk/handoffs/{id}/resolve` closes a request with an optional note.

## The text conversation

Partners that set `"sms_conversation": true` in the branding (`PUT /onboarding-desk/branding`) get the application over text message. A reply to the partner's SMS number continues that lead's application:

- **One question at a time.** The desk asks for the next required item that fits in a text, such as the legal name, structure, registration number, tax number, activity or website, with the reason it is needed. Choices are numbered, so "2" answers "Limited liability company".
- **Refused answers.** A refused answer gets the same message as on the web, and the question again.
- **The rest on the web.** The address, owners and documents are easier on the web, so the desk sends a fresh onboarding link for them. When everything is in, it sends the link to review and submit.
- **Keywords.** HELP (AIDE) says who is writing, that an AI assistant is involved, and how to reach a person or stop. PERSON (PERSONNE) asks for a person. STOP and the other opt-out keywords still opt out of every channel first.

**When the desk replies.** The desk replies only when:
- the phone matches exactly one open lead;
- that lead has a current SMS consent;
- the branding has a partner name and an onboarding page.

**Contact hours.** A reply answers a message the applicant just sent, so contact hours do not hold it back. Outreach still follows the contact rules.

**What is kept.** Every message in both directions goes into the encrypted transcript. Nothing the applicant writes is given to a model.

## The partner's side

| Call | Does |
|---|---|
| `GET /onboarding-desk/applications?status=` | Applications, newest first, with which items are answered; no answers |
| `GET /onboarding-desk/applications/{id}` | One application with its answers, documents and transcript, and, once complete, the `business_case` application |
| `GET /onboarding-desk/applications/{id}/documents/{document_id}` | An applicant's document as a download |

Reading needs `audit:read`. Changing the application policy or resolving a hand-off needs `approvals:write`, and the policy also needs a tenant administrator.

**The governed-case application.** It uses:
- the registered address's state or province as the jurisdiction, falling back to the lead's region;
- the tax and registration numbers as identifiers: `us_ein`, `us_state_file_number`, `ca_business_number`, `ca_corporation_number`, or `ca_qc_neq` in Quebec;
- the owners as declared owners.

The owners' roles stay with the desk.

## Records

Migration `v6z98_onboarding_applications` adds three tenant-scoped tables under forced row-level security:

| Table | Holds |
|---|---|
| `onboarding_desk_applications` | One row per lead: status, merchant type, country, region, locale, encrypted answers and transcript, which items are answered, the item the text conversation is waiting on |
| `onboarding_desk_documents` | Each upload: item, safe file name, type, size, SHA-256, encrypted content, read result, encrypted suggestions |
| `onboarding_desk_handoffs` | Requests for a person: channel, encrypted message, status, who resolved it and their note; at most one open per application |

Answers, transcripts, documents and messages are encrypted under the tenant key before they are stored. Writes to an application take a lock on its row, so answers given on two channels at once are never lost.

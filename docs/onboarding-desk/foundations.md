# Merchant onboarding desk: foundations

The merchant onboarding desk turns a payment partner's opted-in leads into verified, reviewed merchants. This page covers the foundations every later step relies on: leads, per-channel consent, opt-outs, the contact policy, and signed lead intake. [Outreach](outreach.md), [applications and the guided conversation](applications.md), and the hand-off into a governed case build on these.

The desk is off by default. With `AGENTICORG_ONBOARDING_DESK_ENABLED` unset, `GET /api/v1/onboarding-desk/status` answers `enabled: false` and every other onboarding desk route, the signed webhook included, is not found.

## Who may do what

| Action | Needs |
|---|---|
| Read status, leads, consents, sources, the policy and contact decisions | `audit:read` |
| Create or import leads, record or withdraw consent, record an opt-out | `approvals:write` |
| Change the contact policy or create a lead source | `approvals:write` and a tenant administrator |

## Leads

A lead names a business in the United States or Canada, its state or province, and at least one way to reach a person there: an email address or a North American phone number. Optional fields are the contact's name, the locale (`en` or `fr`), a merchant type and an external reference.

- **Locale.** Leads in Quebec default to French.
- **Encryption.** Contact details are encrypted under the tenant key before they are stored. Lists and lead views show masked forms only.
- **Idempotency.** A lead with an external reference is stored once per source, so a retried delivery returns the existing lead.

```bash
curl -s -X POST https://app.example.test/api/v1/onboarding-desk/leads \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "business_name": "Northwind Coffee LLC",
    "contact_name": "Avery Example",
    "email": "owner@northwind.example",
    "phone": "+1 415 555 0134",
    "country": "US",
    "region": "CA",
    "merchant_type": "cafe",
    "external_ref": "form-2026-0042",
    "consents": [{
      "channel": "sms",
      "wording": "Yes, text me about setting up card payments. Msg & data rates may apply. Reply STOP to opt out.",
      "method": "web_form",
      "source_ref": "https://partner.example/apply",
      "captured_at": "2026-10-26T15:04:00Z"
    }]
  }'
```

## Consent

Each consent belongs to one channel: `sms`, `email`, `whatsapp` or `voice`. It records:

- the exact wording the person agreed to, and its SHA-256 hash;
- the capture method: `web_form`, `crm_import`, `csv_attestation`, `inbound_message`, `recorded_call` or `paper_form`;
- where it was captured, and when;
- an optional expiry;
- whether it was captured in writing.

**Voice needs written consent.** An AI or prerecorded voice call needs prior express written consent, so a voice consent counts only when it was captured in writing.

**Withdrawn consents are kept.** `POST /leads/{id}/consents/withdraw` marks a lead's consents withdrawn for one channel or all channels, and the rows stay as a record.

## Opt-outs

`POST /onboarding-desk/opt-outs` suppresses an email address or phone number on one channel or everywhere. It:

1. stores a keyed hash of the contact, never the contact itself;
2. withdraws every matching lead's consents for that channel;
3. marks those leads opted out, when the opt-out covers every channel.

A later lead with the same contact arrives already opted out. Inbound replies of STOP, STOPALL, UNSUBSCRIBE, CANCEL, END, QUIT, REVOKE, OPTOUT, ARRET, ARRETER or DESABONNER count as opt-outs, ignoring case and accents. The outreach step applies them automatically.

## The contact policy and the contact decision

`GET /leads/{id}/contact-decision?channel=sms` says whether the desk may contact a lead on that channel now. If not, it returns the reason and, for hours, the next permitted time. The first rule that refuses wins:

| Reason | Meaning |
|---|---|
| `country_not_supported`, `region_required` | Only the United States and Canada, with a known state or province |
| `channel_not_allowed` | The partner policy does not use this channel |
| `opted_out` | The lead or its contact is suppressed |
| `no_email`, `no_phone` | No contact detail for the channel |
| `no_consent`, `consent_expired` | No current consent for the channel |
| `voice_needs_written_consent` | A voice consent not captured in writing |
| `reminder_limit`, `reminder_too_soon` | The partner's reminder limit or spacing |
| `attempt_limit` | Too many SMS, WhatsApp or voice attempts in 24 hours |
| `outside_hours` | Outside the permitted hours in the recipient's local time |
| `channel_not_initiated` | The web channel is opened by the applicant, never by the desk |

Contact hours and attempts start from these floors. They are a conservative reading to confirm with counsel, not legal advice:

| Where | Hours, recipient's local time | Attempts in 24 hours |
|---|---|---|
| United States | 08:00 to 21:00 | 3 |
| Florida, Oklahoma, Maryland | 08:00 to 20:00 | 3 |
| Canada | 09:00 to 21:30 on weekdays, 10:00 to 18:00 at weekends | 3 |

Where a state or province spans several time zones, only the hours allowed in every zone are used. Email is not bound to hours, but it still needs consent and no suppression.

`PUT /onboarding-desk/policy` can only narrow these floors. It accepts:
- the channels to use;
- a later start or an earlier end;
- fewer attempts;
- no weekends;
- a reminder limit of up to three, with at least four hours between reminders.

## Lead sources and the signed webhook

A tenant administrator creates a lead source with `POST /onboarding-desk/lead-sources` and `{"name": "Website form", "kind": "webhook"}`. The response carries the signing secret once, plus the webhook path. The secret is stored encrypted and never shown again.

The partner's form posts one lead, or `{"leads": [...]}` with up to 100 leads, to `POST /api/v1/onboarding-desk/webhooks/leads/{tenant_id}/{source_id}`, signed like this:

```text
X-AgenticOrg-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "<t>." + raw body, keyed with the source secret>
```

A missing, wrong or stale signature is refused with 401. Signatures more than five minutes old count as stale. The body is limited to 256 KB.

## CSV import

`POST /onboarding-desk/leads/import` accepts a UTF-8 CSV with these columns:

`business_name`, `contact_name`, `email`, `phone`, `country`, `region`, `locale`, `merchant_type`, `external_ref`, `consent_channels`, `consent_captured_at` and `consent_source_ref`.

The file may hold at most 2,000 rows and 1 MB.

- **Consent needs an attestation.** Consents are recorded only when the import carries an attestation: the form fields `consent_wording`, `consent_method` and `consent_written`, stating the wording and method used to collect them. Without one, rows are imported with no consent and cannot be contacted.
- **Bad rows do not stop the import.** Rows that fail validation are reported with their row number and field, and the rest are imported.

## Records

Six tenant-scoped tables under forced row-level security, from migration `v6z96_onboarding_desk`:

| Table | Holds |
|---|---|
| `onboarding_desk_leads` | Business, country, region, locale, merchant type, encrypted contact, masked contact, keyed hashes, status |
| `onboarding_desk_consents` | Channel, wording and hash, method, source, written flag, captured, expiry, status, withdrawal |
| `onboarding_desk_suppressions` | Keyed contact hash, channel or all, reason, source |
| `onboarding_desk_lead_sources` | Name, kind, encrypted signing secret, enabled |
| `onboarding_desk_policies` | The partner's narrowed contact policy |
| `onboarding_desk_contact_attempts` | Each outreach attempt, used for attempt and reminder limits |

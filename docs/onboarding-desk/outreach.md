# Merchant onboarding desk: outreach

Outreach contacts a partner's leads on the channels they agreed to, in their language, inside the permitted hours, and stops the moment they say so. It builds on the [foundations](foundations.md): every message goes through the same contact decision, so nothing is sent without a current consent, outside the hours, past the attempt or reminder limits, or to a suppressed contact.

Outreach is part of the onboarding desk and is off with it. With `AGENTICORG_ONBOARDING_DESK_ENABLED` unset, every route on this page is not found and the sweep does nothing.

## Who may do what

| Action | Needs |
|---|---|
| Read the branding and a lead's attempts | `audit:read` |
| Contact one lead | `approvals:write` |
| Change the branding or run due outreach now | `approvals:write` and a tenant administrator |

## Branding

Every message says who is writing. `PUT /onboarding-desk/branding` sets the partner's outreach identity:

| Field | Meaning |
|---|---|
| `partner_name` | The name every message opens with, up to 60 characters |
| `onboarding_url` | The partner's onboarding page; an https URL with no query or fragment |
| `postal_address` | The partner's postal address, required for email |
| `auto_contact` | Whether the sweep contacts due leads on its own; off by default |
| `first_contact_delay_minutes` | How long a new lead waits before first contact, 0 to 1,440 |
| `senders` | One sender per channel, each naming a configured connector |

Senders reuse the tenant's connectors, so their credentials stay where they already are:

| Channel | Connector | Sender fields |
|---|---|---|
| `sms` | Twilio, e.g. `registry-twilio` | `sender`: the SMS number in E.164 form |
| `email` | SendGrid, e.g. `registry-sendgrid` | `sender`: the from address |
| `whatsapp` | WhatsApp Business, e.g. `registry-whatsapp` | `template` and `language`: the approved template |

```bash
curl -s -X PUT https://app.example.test/api/v1/onboarding-desk/branding \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{
    "partner_name": "Northwind Payments",
    "onboarding_url": "https://apply.northwind.example/start",
    "postal_address": "100 Example Street, Toronto ON M5V 0A1",
    "auto_contact": false,
    "first_contact_delay_minutes": 5,
    "senders": {
      "sms": {"connector": "registry-twilio", "sender": "+1 416 555 0100"},
      "email": {"connector": "registry-sendgrid", "sender": "hello@northwind.example"}
    }
  }'
```

Branding is stored with the contact policy, and changing one leaves the other as it was. Voice is not an outreach channel in this release.

## Messages

Messages are written in English or French, following the lead's locale. Each one:

- opens with the partner's name;
- says an AI assistant can help, and that a person is available;
- carries a signed onboarding link for that lead, valid for 14 days;
- says how to stop: "Reply STOP" by SMS, "STOP ou ARRET" in French, or an unsubscribe link and the partner's postal address by email.

A first contact thanks the lead for their interest; a reminder says the application is waiting. An SMS longer than 480 characters is refused rather than cut, so a long partner name or onboarding URL shows up as `message_invalid` on the attempt.

WhatsApp first contact uses the partner's approved template, filled with two parameters in this order: the partner name and the onboarding link. The template's own wording is approved by Meta, so it must carry the disclosure and the way to stop.

## Contacting a lead

`POST /onboarding-desk/leads/{id}/contact` contacts one lead now. With `{"channel": "sms"}` it uses that channel; without a channel it tries the policy's channels in order and uses the first one the branding, consents, hours and limits allow. `{"kind": "reminder"}` sends a reminder, which counts toward the reminder limit and spacing.

```json
{"sent": false, "reason": "outside_hours", "channel": "sms", "attempt_id": null,
 "next_allowed_at": "2026-10-27T13:00:00+00:00"}
```

The answer says whether a message went out and, when it did not, why:

- **A contact rule refused it.** The reason is the contact decision's reason, such as `no_consent` or `outside_hours`. When it is a matter of time, the lead is scheduled for the next permitted moment.
- **The branding cannot send.** `branding_partner_name_missing`, `branding_onboarding_url_missing`, `sender_not_configured` or `branding_postal_address_missing`.
- **The provider failed.** `sender_connector_not_configured`, `send_failed:<error type>` or `provider_error:<detail>`. The lead is retried an hour later. Only messages that went out count toward the 24-hour attempt limit.
- **Someone else is contacting it.** A second request for the same lead within ten minutes of a queued attempt is refused with 409 `contact_in_progress`, so two workers never message one lead twice.
- **The lead is closed.** An opted-out, submitted or closed lead is never contacted: `lead_opted_out`, `lead_submitted` or `lead_closed`.

`GET /onboarding-desk/leads/{id}/attempts` lists every attempt with its channel, kind, status, refusal reason and provider reference.

## Handing over the link yourself

A partner whose own form or call centre is already talking to the person can take them straight to the application instead of waiting for a message. `POST /onboarding-desk/leads/{id}/apply-link` signs a link for that lead and sends nothing:

```json
{"token": "eyJ...", "url": "https://apply.partner.example/onboarding?t=eyJ...",
 "expires_at": "2026-11-10T14:02:00+00:00"}
```

- **The same link as a message carries.** It opens the same application, is valid for 14 days and is bound to its purpose, so it cannot unsubscribe anyone.
- **`url` needs an onboarding page.** It is `null` until the branding names one; the `token` alone goes to an embedded page as its `link-token` attribute (see [the web page](web-page.md)).
- **Not a contact.** No consent is needed and no attempt is recorded, because the desk contacts no one. The request is logged with the operator who made it.
- **Who may ask.** Any signed-in user of the tenant, as for contacting a lead.

## Due outreach and the sweep

A lead is due when its next contact time has passed, or when it is new, has never been scheduled and has waited the first-contact delay. New leads get a first contact; the rest get reminders, at most the policy's reminder limit and no sooner than its spacing.

- `POST /onboarding-desk/outreach/run` with `{"limit": 50}` contacts the tenant's due leads now and returns how many were due, sent, deferred to a later time, or refused.
- A beat task, `sweep-onboarding-desk-outreach`, runs every minute and does the same for each tenant whose branding sets `auto_contact` to true.

Recording a new consent makes a lead due again: a new lead after the first-contact delay, any other lead straight away. Either way the contact rules still decide when the message goes out.

## Stopping

**SMS replies.** Point the Twilio number's incoming-message webhook at:

```text
POST https://<api host>/api/v1/onboarding-desk/webhooks/twilio/{tenant_id}
```

The request must carry a valid `X-Twilio-Signature`, checked with the auth token of the connector named by the SMS sender; anything else is refused with 403. A reply of STOP or another opt-out keyword suppresses the sender's number on every channel and marks matching leads opted out. Any other reply marks the lead engaged, so a person can follow up.

**Email.** Each email carries an unsubscribe link valid for a year. Opening it shows a confirmation page in the lead's language and changes nothing, so a mail scanner that follows links cannot unsubscribe anyone. Confirming on that page suppresses the lead's email address for email.

Links are signed with the platform secret and bound to their purpose: an unsubscribe link cannot open an application, and an onboarding link cannot unsubscribe anyone.

## Records

Migration `v6z97_onboarding_outreach` adds two columns to `onboarding_desk_leads`:

| Column | Holds |
|---|---|
| `next_contact_at` | When the lead is next due, or empty when it waits on something other than time |
| `contacted_at` | When the first message went out |

It also adds an index on the tenant and next contact time for due leads, and a read policy that lets the sweep find tenants with a desk policy across tenants. Attempts, sent or refused, are kept in `onboarding_desk_contact_attempts`.

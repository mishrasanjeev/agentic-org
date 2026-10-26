# Merchant onboarding desk: the applicant page

The applicant page is where a merchant fills in their [application](applications.md) on the web. It shows the partner's name and colour, says an AI assistant is involved and that a person is available, and links the partner's privacy notice. Each item comes with why it is needed, and answers are saved as the applicant goes. The applicant can upload documents and confirm the values they suggest, ask for a person, and submit. It speaks English or French, following the lead's language.

The page is a dependency-free web element, `<agenticorg-onboarding>`. The partner can place it on their own site, under their own domain, or send applicants to the hosted page.

## Branding

The page uses these fields from `PUT /onboarding-desk/branding`, as well as the partner name:

| Field | Meaning |
|---|---|
| `onboarding_url` | Where outreach links send applicants: the partner's page with the element, or the hosted page |
| `brand_color` | The page's accent colour, `#RRGGBB` |
| `privacy_url` | The partner's privacy notice, an https URL; linked at the top of the page |

## On the partner's own site

Point `onboarding_url` at a page on the partner's site, and put the element there:

```html
<script src="https://app.agenticorg.ai/onboarding-desk/apply.js" defer></script>
<agenticorg-onboarding api-base="https://app.agenticorg.ai/api/v1"></agenticorg-onboarding>
```

Outreach links arrive as `<onboarding_url>?t=<signed token>`. When the element opens, it:
1. reads the token from the page's `t` parameter;
2. removes the token from the address bar;
3. exchanges it for a two-hour applicant session.

The session stays in memory and is never written to storage or a cookie. A page that receives the token another way can pass it in a `link-token` attribute.

**What the partner's site needs:**
- Its content security policy allows the script from `https://app.agenticorg.ai` and connections to it.
- Nothing else. The applicant routes accept calls from any site, authenticated by the session header alone. They never use cookies, so no browser credentials are sent or accepted.

**Events for the host page.** The element fires:

| Event | When |
|---|---|
| `onboarding:ready` | The application opened |
| `onboarding:person-requested` | The applicant asked for a person |
| `onboarding:submitted` | The application was submitted |

## The hosted page

A partner without a page of its own can set `onboarding_url` to `https://app.agenticorg.ai/onboarding-desk/apply`.

The hosted page:
- carries the element and nothing else;
- runs no inline script;
- is not indexed;
- sends no referrer.

## What the page does

- **Every item.** Each item the partner's policy asks for appears with its label, why it is needed, and whether it is required. Choices are lists. The address has street, city, state or province, and postal code fields. Owners are a list the applicant can add to. Documents have an upload button.
- **Saving.** Saving an item sends just that item. A refused answer shows the server's message in the applicant's language. Other items keep what the applicant typed but has not yet saved.
- **Documents.** Each document lists the values it suggests, each with a tick box. Only the ticked values are saved.
- **Progress.** The page shows how many required items are done. Submit stays disabled until every required item is in.
- **Talking to a person.** "Talk to a person" takes an optional message and opens a hand-off.
- **Messages.** The page explains each of these: an expired link, an ended session, a closed application, a missing link, and an unreachable server.

Nothing from the server or a document is treated as markup. Every string is set as text. The partner colour and privacy link are used only when they are a valid colour and an https URL.

## Limits

Uploads are PDF, PNG or JPEG files of up to 8 MB. The reverse proxy in front of `/api/` accepts requests of up to 10 MB, including the upload's envelope.

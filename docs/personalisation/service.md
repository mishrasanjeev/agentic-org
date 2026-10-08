# Personalisation

Behind `personalisation_enabled` (default off; `AGENTICORG_PERSONALISATION_ENABLED`). Off,
`GET /personalisation/status` answers `enabled: false` and every other personalisation route is
not found; nothing else changes.

## What it does

The service renders plain-text content for one subject and one purpose from that subject's
profile, and only when the subject holds a valid consent for that purpose. Every render and
every refusal is recorded with the consent it relied on, the rule that chose the content, the
names of the attributes used and a hash of the output. The code is in
`core/personalisation/rules.py` (checks and evaluation, no store) and
`core/personalisation/service.py` (consents, profiles, rules, rendering, events); the tables are
in `core/models/personalisation.py`, migration `v6z77`.

A **subject** is the tenant's own customer reference: 1 to 128 characters of letters, digits and
`. _ : -`. An e-mail address is refused, so the store never keys on one.

## Consent

A consent is the current record for a (subject, purpose) pair, one row per pair. Purposes are
`marketing`, `service`, `collections`, `retention` and `onboarding`.

- A **grant** (`PUT /personalisation/consents`) needs the evidence of where the consent was
  captured (up to 500 characters, for example a form reference or a channel and date) and may
  carry an expiry in the future. A grant on a withdrawn record makes it granted again with the
  new evidence and expiry.
- A **withdrawal** (`POST /personalisation/consents/withdraw`) keeps the row and marks it
  `withdrawn` with the time. Withdrawing twice changes nothing.
- A consent is **valid** only while its status is `granted`, it has no withdrawal time and its
  expiry (if any) has not passed. A consent for another purpose never counts.

A render reads the consent under a shared row lock, so a withdrawal that arrives during a render
waits for it to finish and every later render sees it.

## Profiles

`PUT /personalisation/profiles` replaces a subject's attributes: a flat object of names
(`a-z` first, then `a-z 0-9 _`, at most 64 characters) to strings (at most 500 characters),
numbers or true/false; at most 100 attributes and 16,000 characters of JSON. The attributes are
encrypted for the tenant (`encrypt_for_tenant`) before any row is locked and kept as
`{"_encrypted": "..."}`; no attribute value is stored in clear. The write answers only the
attribute names. `GET /personalisation/profiles?subject_ref=` decrypts them for an authorised
reader. A row that cannot be decrypted refuses (500 `profile_unreadable`); it is never read as
empty.

## Rules

A rule (`/personalisation/rules`) has a name unique for the tenant, a purpose, a priority (0 to
10,000; lower is tried first, then by name), an enabled flag, conditions, a variant and the
attributes it may use:

```json
{
  "name": "gold-welcome",
  "purpose": "marketing",
  "priority": 10,
  "conditions": [{"attribute": "segment", "op": "eq", "value": "gold"}],
  "variant": {"template": "{{first_name}}, a new offer for you in {{city}}", "label": "Gold welcome"},
  "allowed_attributes": ["first_name", "city", "segment"]
}
```

Condition operators: `eq`, `ne`, `in` (a list of up to 50 values), `gte` and `lte` (numbers, or
text such as ISO dates compared as text) and `exists` (true or false). A condition on an absent
or empty attribute holds only for `exists: false`; values of different kinds (a flag and a
number, a number and text) never match. A rule declares every attribute it reads: an attribute
named in its conditions or its template that is not in `allowed_attributes` refuses the rule (422
`attribute_not_allowed`), on create and on every change. The name does not change.

## Rendering

`POST /personalisation/render` with `subject_ref`, `purpose`, `channel` (`email`, `sms`, `push`,
`in_app`, `web`, `letter`, `whatsapp`, `voice`, `branch`) and optionally a rule name (`rule`) or
the caller's own `template`, never both:

1. Without a valid consent for the subject and purpose the render is refused with 403
   `consent_required` and a refused event is recorded. No profile is read.
2. The profile is decrypted (a subject without one has no attributes).
3. Without a template, the first enabled rule for the purpose by priority whose conditions match
   is used, or the named rule, which must exist, be for this purpose, be enabled and match.
4. The template's placeholders are substituted. A placeholder must be allowed (the rule's
   `allowed_attributes`; for a caller's template, the tenant's list in the business console
   setting `personalisation.template_attributes`, empty by default and while the console is off)
   and present and non-empty in
   the profile. Anything else refuses the render (422 `placeholder_not_allowed` or
   `placeholder_unresolved`); a placeholder is never rendered blank. A malformed placeholder is
   refused as well. Templates are at most 4,000 characters and content at most 8,000. Output is
   plain text; true and false show as yes and no.
5. An event is recorded and the answer is
   `{content, content_hash, rule: {id, name, label}, attributes_used, consent: {id, purpose,
   expires_at}, channel, preview, event_id}`.

`attributes_used` lists the names substituted into the content and the names the chosen rule's
conditions read. Every refusal after the input checks (no consent, no rule, a rule that does not
fit, a placeholder refused) is recorded as a refused event with its code and without attributes
or a hash. Invalid input (an unknown purpose or channel, a malformed subject or template) is
answered 422 without an event.

`preview: true` evaluates exactly the same, still requires a valid consent, and records nothing;
its `event_id` is null.

## Events

`GET /personalisation/events?subject_ref=&limit=` (newest first, at most 200) returns each
event's subject, purpose, consent, rule, `attributes_used` (names only, never values), content
hash, channel, outcome (`rendered` or `refused`), refusal code, actor and time. The content itself
is not kept. Deleting a rule keeps its events with the rule cleared.

## API and access

| Method and path | Scope |
| --- | --- |
| `GET /personalisation/status` | read |
| `PUT /personalisation/consents`, `POST /personalisation/consents/withdraw` | write |
| `GET /personalisation/consents?subject_ref=` | read |
| `PUT /personalisation/profiles` | write |
| `GET /personalisation/profiles?subject_ref=` | read |
| `GET /personalisation/rules?purpose=`, `POST /personalisation/rules` | read, write |
| `PATCH /personalisation/rules/{id}`, `DELETE /personalisation/rules/{id}` | write |
| `POST /personalisation/render` | write |
| `GET /personalisation/events?subject_ref=&limit=` | read |

The family `personalisation` maps reads to `audit:read` and writes to `approvals:write`
(`api/route_enforcement.py`). Granting consent, writing a profile and creating, changing or
deleting a rule need an identified user in the session and fail closed (401 `actor_required`)
without one; the user is recorded on the row. A withdrawal and a render record the user when the
session names one. All four tables are tenant scoped under forced row-level security, and every
foreign key of the events table has a leading index. Telemetry carries the purpose, the channel
and the outcome, never a subject or an attribute.

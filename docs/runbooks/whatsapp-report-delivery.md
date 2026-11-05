# WhatsApp delivery of scheduled reports

Scheduled reports (the Report Scheduler, including Insights views) can be sent to a WhatsApp number. The file is
uploaded to the WhatsApp Cloud API and sent to the number on the schedule (`core/reports/delivery.py`).

## Why a template

WhatsApp delivers a business-initiated free-form message only to someone who wrote to the business number in the
last 24 hours. A scheduled report goes to people who usually have not, so a plain document is refused with error
131047 ("Re-engagement message"). An approved message template is delivered to anyone who agreed to receive
messages from the business.

With `AGENTICORG_WHATSAPP_REPORT_TEMPLATE` set, every report goes as that template, with the file as its document
header. Without it, the file goes as a plain document, which arrives only inside the 24-hour window; outside it,
the run records the refusal and says to set a template.

## What the organisation provides (owner action)

The platform does not hold a WhatsApp Business account. The organisation that wants WhatsApp delivery provides:

1. A WhatsApp Business account with a phone number registered on the Cloud API.
2. A permanent access token for a system user with `whatsapp_business_messaging` on that account, and the phone
   number's ID.
3. An approved template in the Utility category with:
   - a header of type Document;
   - a body with one variable, `{{1}}`, which receives the report's name (for example "Insights report:
     Finance"). If the body has no variable, set `AGENTICORG_WHATSAPP_REPORT_TEMPLATE_BODY_TEXT=false`;
   - a template name of lower-case letters, digits and underscores, and its language code (for example `en`
     or `hi`).
4. The recipients' consent to receive reports on WhatsApp.

## Configure

Set on the API, worker and beat services (Secret Manager for the token):

| Setting | Value |
| --- | --- |
| `WHATSAPP_TOKEN` | The system user's access token (secret) |
| `WHATSAPP_PHONE_ID` | The phone number ID |
| `AGENTICORG_WHATSAPP_REPORT_TEMPLATE` | The approved template's name |
| `AGENTICORG_WHATSAPP_REPORT_TEMPLATE_LANGUAGE` | Its language code (default `en`) |
| `AGENTICORG_WHATSAPP_REPORT_TEMPLATE_BODY_TEXT` | `true` when the body has the `{{1}}` variable (default) |
| `AGENTICORG_WHATSAPP_GRAPH_API_VERSION` | The Graph API version (default `v23.0`); move it forward before Meta retires the version in use |

## Check it

1. Add a WhatsApp channel (E.164 number, for example `+919876543210`) to a weekly Insights view schedule and run
   it now from the Report Scheduler.
2. The run history shows `sent` for the channel, or `failed` with WhatsApp's code and title (for example
   `132001 template name does not exist`, `131047` outside the window, `190` an invalid token).
3. Logs carry only the number's last four digits.

## Limits

- WhatsApp accepts PDF, Excel, Word, PowerPoint and text documents. ZIP packs (the finance, evidence and partner
  packs) are not sent on WhatsApp: the channel is skipped with that reason and the file stays on the Downloads page
  for seven days.
- WhatsApp's per-number messaging limits and template quality rating apply; a paused template fails every send
  until Meta restores it.

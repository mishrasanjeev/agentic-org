# CA Firms and CMO Connector User Guide

Updated: 2026-05-30

This guide documents the supported prompt and JSON shapes for the CA Firms and
CMO connector workflows. It intentionally excludes credentials and tokens.

## Setup Order

1. Create and test connector credentials first.
2. Create or update the agent with only the connector tools it should use.
3. Run a read-only prompt first, then run create/update/delete prompts.
4. If a connector test fails, fix that connector before promoting or resuming
   the agent.

## Zoho Books

Use Zoho Books India with API base URL `https://www.zohoapis.in/books/v3` and
token URL `https://accounts.zoho.in/oauth/v2/token`.

Required connector fields:

| Field | Required | Notes |
|---|---:|---|
| `client_id` | Yes | OAuth app client ID |
| `client_secret` | Yes | OAuth app client secret |
| `refresh_token` | Yes | Required for production readiness |
| `organization_id` | Yes | Zoho Books organization ID |
| `region` | Optional | Use `in` for India; inferred from Zoho India URLs |

Supported CA prompt examples:

| Workflow | Run Agent prompt | Tool JSON shape |
|---|---|---|
| List vendors | `Get all vendors from Zoho Books` | `{"page":1}` |
| Create vendor | `Create a Zoho Books vendor named New Bharat Accountant Firm with email accounts@example.com` | `{"contact_name":"New Bharat Accountant Firm","email":"accounts@example.com","phone":"7827443304"}` |
| Create item | `Create item Audit Services for the mentioned vendor at rate 5000` | `{"name":"Audit Services","rate":5000,"vendor_id":"<vendor_id>"}` |
| Create bill | `Create bill on Zoho Books for vendor <vendor_id> with item <item_id>` | `{"vendor_id":"<vendor_id>","date":"2026-05-30","line_items":[{"item_id":"<item_id>","rate":5000,"quantity":1}]}` |

Troubleshooting:

| Error shown | Meaning | Fix |
|---|---|---|
| `invalid_access_token` | Upstream rejected token | Re-authorize or rotate token |
| `expired_token` | OAuth token expired and refresh failed | Recreate refresh token |
| `missing_permissions` | OAuth scope/account lacks access | Add Zoho Books scopes and reconnect |
| `invalid_payload` | Required field or payload shape is wrong | Use the JSON shapes above |
| `invalid_endpoint_or_resource` | Wrong base URL or record ID | Verify region URL and IDs |

## HubSpot CRM

Supported contact prompt examples:

| Workflow | Run Agent / Chat prompt | Tool JSON shape |
|---|---|---|
| List contacts | `List the first 5 contacts from HubSpot CRM. Show name, email, and company.` | `{"limit":5,"properties":"firstname,lastname,email,company"}` |
| Create contact | `Create contact on HubSpot for Rajeev Sharma, email rajeev@example.com, phone 7827443304, company New Bharat Accountant Firm.` | `{"properties":{"email":"rajeev@example.com","firstname":"Rajeev","lastname":"Sharma","phone":"7827443304","company":"New Bharat Accountant Firm"}}` |
| Update contact | `Update HubSpot contact <contact_id> phone to 9999999999.` | `{"contact_id":"<contact_id>","properties":{"phone":"9999999999"}}` |
| Delete contact | `Delete HubSpot contact <contact_id>.` | `{"contact_id":"<contact_id>"}` |
| Assign owner | `Assign owner <owner_id> to contact <contact_id>.` | `{"contact_id":"<contact_id>","owner_id":"<owner_id>"}` |
| Associate contact to company | `Associate contact <contact_id> to company <company_id>.` | `{"contact_id":"<contact_id>","company_id":"<company_id>"}` |
| List owners | `List HubSpot owners for assignment.` | `{"limit":100}` |

For `create_contact`, both direct fields and native HubSpot `properties` are
valid. The nested `properties.email` field is required and must be a valid
email address.

## CMO / Marketing Connectors

Start with read-only checks before write operations:

| Connector | Read prompt | Write prompt |
|---|---|---|
| HubSpot | `List first 5 contacts from HubSpot` | `Create HubSpot contact with properties ...` |
| Google Ads | `Show campaign performance for last 7 days` | `Update campaign budget after approval` |
| GA4 | `Show sessions and conversions for last 7 days` | Read-only connector |
| SendGrid | `Show recent campaign stats` | `Send campaign/test email after approval` |

Marketing write operations should be gated by human approval when they spend
budget, send external communications, delete CRM records, or mutate campaign
configuration.

## Expected Responses

Successful connector calls return structured objects such as:

```json
{
  "status": "created",
  "id": "provider_record_id"
}
```

Connector failures must return the provider reason in the UI:

```json
{
  "error": "invalid_payload",
  "message": "Upstream hubspot API returned HTTP 400: email is required",
  "connector": "hubspot",
  "tool": "create_contact"
}
```

Do not treat a failed tool as a completed task. The run result must show the
failed connector, tool name, status code where available, and provider message.

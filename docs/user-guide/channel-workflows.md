## From a message to useful work

Create an agent with a purpose, knowledge and permitted tools, or choose an existing workflow. Open **Channels** in the dashboard. Choose a provider, choose the destination, and enter credentials from your own provider account. Connections start disabled. For Telegram, select **Connect Telegram** to check the bot and register its callback. For other providers, register the displayed HTTPS callback with the provider before enabling the connection.

```flow
Create a provider account | You own the account, permissions and billing. | Owner: Workspace user
Choose an agent or workflow | Agent creators connect agents they can manage. Workflow connections require an administrator. | Owner: Workspace user
Save protected credentials | AgenticOrg encrypts credentials; it never returns them in the channel list. | Owner: AgenticOrg
Register and enable the callback | Use the callback URL and the provider's signing or verification settings. | Owner: Workspace user | If blocked: Check the provider application permissions and account approval.
Link a private sender | Generate a one-time command and send it from your private messaging account. | Owner: Workspace user | Human decision: Delegate your current account permissions.
Run a bounded task | A durable worker invokes the existing agent or workflow execution path. | Owner: AgenticOrg | If blocked: Review the message, identity, provider signature or run error.
Review results and approvals | Open the inbox and run history. Sensitive actions retain their existing approval requirements. | Owner: Workspace user
```

Unlinked senders do not acquire your permissions. Their text enters a review inbox. Approving one message authorizes that message only. A provider signature identifies an authentic callback, not a human user. Linking is available for private text messages, not Telegram groups or email From addresses. Link commands expire after ten minutes, are single-use, and delegate for thirty days. Disabling a connection or rotating credentials revokes links and queued delegated work. An already-running external action cannot be undone by disabling its connection.

## Credentials belong to you

A completed channel delivery proves only the configured target ran. A workflow
that copies the message is a transport test, not proof of AI reasoning or tool
execution. For reasoning acceptance, select a dedicated tool-free agent with a
working tenant AI credential, send a synthetic question with a known answer and
inspect the model usage and output in run history. Agree on a spending limit
before making a paid model call. Do not reuse customer workflows for that test.

Enter secrets only in the signed-in credential form. Never put tokens, API keys, webhook secrets, passwords or one-time link commands in chat, prompts, repository files or support screenshots. To arrange assisted testing, share only the provider name, account approval status, test sender or destination you authorize, and a sanitized request ID. Use the credential form or your organization's approved secret-management process for the secret itself. Choose dedicated test accounts, minimum permissions and spending limits.

The first version accepts bounded text messages. Images, files and voice notes on these messaging callbacks do not start tasks. Use the knowledge-upload and voice setup paths for document processing and calls. Native messaging replies, when enabled, contain task status only. Results stay in the authenticated AgenticOrg inbox; private knowledge and tool outputs are not broadcast to the channel. Email and generic gateways have no native outbound reply in this version.

## Google reasoning model

The default for newly created agents is `gemini-3.5-flash-lite`. Existing agents keep their explicitly selected model. Administrators can select a different model, fallback and provider. Obtain a Gemini API key through [Google AI Studio](https://aistudio.google.com/), enable the account and billing appropriate for your usage, and save it under **Settings > AI credentials > Gemini**. Use company-scoped credentials for company agents and the appropriate tenant credential for personal agents. Model availability and quota must be verified with your account before relying on it.

Gemini reasoning does not replace speech recognition or speech synthesis. This model does not generate audio; configure those providers separately. Operator overrides remain available through `AGENTICORG_LLM_PRIMARY` and `AGENTICORG_LLM_FALLBACK`.

## WhatsApp and Meta Messenger

Create a Meta developer application and the appropriate business assets. For WhatsApp, attach your business account and phone number to the Cloud API. For Messenger, connect the Facebook Page and subscribe the app to its messaging callbacks. Complete the platform's business verification, application review and messaging permissions before serving external users.

Save the app secret, access token, a verification token you generate, the destination ID and a supported Graph API version. **Receiver ID** means the phone-number ID for WhatsApp, not the telephone number; for Messenger it means the Page ID. Register the callback URL and matching verification token. Subscribe to messages. AgenticOrg verifies the raw-body `X-Hub-Signature-256` signature and checks receiver scope before storing any message. Use the provider's development/test recipients first. Test tokens can expire; replace them with the account's approved production credential when ready.

See [WhatsApp setup](https://developers.facebook.com/docs/whatsapp/cloud-api/get-started) and [Messenger setup](https://developers.facebook.com/docs/messenger-platform/getting-started/). Provider restrictions, customer-contact windows and billing still apply. An AgenticOrg connection is not platform approval. Use a dedicated verification token, never an API key or app secret: Meta sends it in its verification URL, which provider or infrastructure request logs may retain. Application access logs redact it.

## Telegram

**Already connected? Reuse it.** Open the existing connection under **Channels**
instead of saving the same bot as a second connection. Preserve its configured
agent or workflow destination. A bot has one Telegram webhook;
creating another AgenticOrg connection does not move that webhook or replace the
existing target. Keep an unused duplicate disabled.

Open [BotFather](https://t.me/BotFather), send `/newbot`, and follow its prompts to create a dedicated bot. In AgenticOrg, choose Telegram and the destination agent or workflow, enter the bot token, select **Generate secret**, and save. Select **Connect Telegram**: AgenticOrg checks the bot account, registers the HTTPS callback and verifies its registration before enabling the connection. Tokens never need to appear in a browser URL or terminal command. A bot already connected to another application is refused; use a dedicated bot rather than overwriting that application's callback.

If setup reports `telegram_bot_connected_elsewhere`, its registered webhook does
not match the new connection. This is a safety refusal, not evidence that the
working bot is broken. The existing webhook is not overwritten. Open the original
AgenticOrg connection if you own it; otherwise use a different, dedicated bot.
Do not rotate working credentials, call `deleteWebhook`, poll `getUpdates`, or
replace a production callback just to make an acceptance test pass.

Open a private conversation with the bot, generate **Link my messaging account** in AgenticOrg and send the displayed command from that private account. Then send a small, read-only task. Groups and channel posts deliberately do not inherit a human account's authority. Localhost callbacks cannot be registered with Telegram; use the deployed HTTPS service for the real account test. AgenticOrg supplies `secret_token` and requires the resulting `X-Telegram-Bot-Api-Secret-Token` header. Registration is not proof of delivery: confirm a real message and completed run in the inbox. See the [Bot API](https://core.telegram.org/bots/api#setwebhook).

### Prove reasoning and delivery separately

1. Record the existing connection's destination before testing. A copy-only
   workflow proves message transport, not a model call. Leave a working route
   unchanged unless its owner explicitly authorizes a different destination.
2. For a real reasoning check, use a personal, tool-free shadow agent, a working
   Gemini credential, an explicit model and zero agent retries. Ask a short
   synthetic question, such as `(23 * 7) - 19`, without supplying its answer.
   Request structured JSON with numeric `answer` and `confidence`; check the
   independently calculated answer of 142 without lowering the review threshold.
3. For the existing Telegram path, send one uniquely labeled message from the
   linked private account. Match its new inbox delivery, configured run result
   and the status notice actually received in Telegram. Do not submit an unsigned
   or fabricated callback as proof of a real Telegram delivery.
4. Call the combined Telegram-to-Gemini path verified only if that same delivery
   invokes the configured reasoning agent or an actual model step. Two independent
   successful tests must not be described as a combined reasoning integration.
5. Keep an approved total spending allowance. Zero agent retries does not disable
   SDK transport retries or the separate explanation request. Include those calls
   and thinking/output tokens in the bound; a recorded reasoning estimate is not
   the complete provider invoice. Do not change deployment flags or tenant limits
   to get a passing result.

Telegram's native reply is a status notice. The computed answer, reasoning and
private data remain in the authenticated inbox or agent result, not the Telegram
message. A provider-accepted send and the recipient's confirmation are separate
pieces of delivery evidence.

## Viber

Arrange a Viber bot account through the provider's commercial application process. New bots may require a commercial agreement; a normal personal Viber account is not a bot API credential. Save the bot authentication token. Register the displayed callback through the REST Bot API. AgenticOrg verifies `X-Viber-Content-Signature` against the raw body using that token. Link your private sender and test a text task. See [Viber's bot account and API requirements](https://developers.viber.com/docs/api/rest-bot-api/).

## Inbound email and outbound email tools

For inbound email, create a Mailgun receiving domain, configure the DNS records required by Mailgun, and create a route forwarding a dedicated recipient to the AgenticOrg callback. Save the **webhook signing key**, not the general API key, and the exact receiving address. AgenticOrg checks the timestamp/token signature and recipient; email always requires a human to approve the message before execution. Email From can be spoofed and is not account authentication. Text-only form submissions are supported; attachments are not ingested by this callback. See [Mailgun receiving routes](https://documentation.mailgun.com/docs/mailgun/user-manual/receive-forward-store/).

For outbound email, configure the existing email connector with your own OAuth or provider credential, bind it to the agent and review tool permissions. Inbound receipt does not automatically grant an agent permission to send mail. Start with a dedicated test mailbox and consented recipients.

## Voice and telephone calls

Open **Voice and calls** as an administrator and select the existing voice-enabled agent setup. Create a Twilio account, obtain a voice-capable number, and configure its inbound Voice URL as shown by the voice setup. Save the account credential and configure the supported speech-to-text and text-to-speech providers. Use trial/test destinations first. AgenticOrg's voice path checks signed callbacks and preserves outbound-call approval controls.

Telephony, speech recognition and speech synthesis have separate permissions, quotas and charges. Do not assume that saving a credential proves recognition accuracy or call delivery. Arrange a test number you own or are authorized to call, approve the destination explicitly and run the voice test with that account. No paid call should be made to an unapproved destination. See [Twilio Voice](https://www.twilio.com/docs/voice).

## MCP tools and other channels

Open **MCP tools** to register a remote MCP server, supply its approved authentication and discover its tools. Review discovered tools and attach only the required tools to your agent. A connector credential is not permission to bypass workflow approvals or execute arbitrary writes. Private or insecure URLs are restricted by the transport's network-safety policy. Use a reachable HTTPS server for real provider integrations.

For WeChat or another channel without a native adapter here, use an approved gateway that verifies the channel's native identity/signature first, then forwards the normalized message through **Other channel / signed gateway**. This is not a native WeChat implementation. The gateway must preserve a stable sender identity and a unique event ID; it must not invent a user's identity from an unverified display name.

The gateway body is JSON with `event_id`, `sender_id` and `text`. Send an epoch-seconds `X-AgenticOrg-Timestamp` header and `X-AgenticOrg-Signature`, the hexadecimal HMAC-SHA256 of `timestamp + "." + raw_body` using the stored webhook secret. Callbacks older than five minutes are refused. The URL selects the tenant and binding; a tenant field in the body cannot change that scope. Retries reuse the event ID and exact message contents. Reusing an event ID for different contents is refused.

## Verify before serving customers

Send a small signed test message, confirm it appears in the inbox, link the private sender or approve that one message, and inspect the completed agent/workflow run. Test a bad signature, duplicate message, expired link and disabled connection as well. Expect the same existing approvals for sensitive tools. A timed-out or interrupted execution enters review and is not automatically replayed, because it may already have performed an external action.

Channel agent targets must still be active or in shadow mode when the worker dispatches. Paused, retired, deleted, unavailable or no-longer-owned targets enter review instead of executing. A pause or revocation cannot undo a task already running. Workflow steps retain their existing approval and lifecycle policies.

Local provider-shaped tests prove the platform's wire handling, isolation and execution paths. They do not prove that your provider account is approved or that a real message or call was delivered. Record those account-backed results separately, with sanitized evidence and authorized test contacts.

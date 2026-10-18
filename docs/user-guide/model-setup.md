## What this configuration does

AI provider credentials let AgenticOrg call the chosen model or embedding service for the authorized tenant context. A connector credential and a model credential are different: connecting your accounting system does not authorize model calls, and a model key does not grant access to the accounting system.

The administrator screens are **AI Provider Credentials** and **AI Configuration**. The agent creation wizard also has provider, model and routing settings. Inspect your deployment's options; labels do not guarantee free usage, provider access or regional availability.

## Before adding a provider

Ask IT/security which providers, endpoints, regions and data classes are allowed. Establish billing ownership, retention rules, rate limits and a rotation owner. For BFSI, confirm whether any customer data may leave the bank boundary and which endpoint has been contractually approved. These are organization decisions; the product does not certify your configuration.

## Add and test a credential

1. Open **AI Provider Credentials** as an administrator.
2. Choose **New Credential**.
3. Select the provider and kind (for example LLM or embedding).
4. Enter the provider-issued key through the credential form, not an agent prompt or document.
5. Use a meaningful label that identifies its purpose without revealing customer information.
6. Supply the reviewed base URL where the provider requires it, such as Azure or an OpenAI-compatible endpoint.
7. Save and use the supported credential test. Verify the response and scope instead of assuming save means ready.

Credentials are stored through the platform's protected credential path. The form does not display the raw key again. Treat a masked entry as evidence of storage, not proof that every requested model is available.

## Select a model and routing

In **Create Virtual Employee > Behavior**, choose the LLM provider/model and routing mode. Use a model actually served by the configured endpoint. Automatic routing can select a different tier based on configuration; disabling routing uses the selected model path. Confirm the resulting provider in run evidence before comparing latency or cost.

Tenant administrators can use the [Model Gateway](/docs/model-gateway) for scoped routing/access policies, dry runs and limits. It is distinct from adding provider credentials: a policy cannot make an unconfigured provider or model available.

Test a short task with the target company selected. Then test an agent or workflow using that same context. Do not validate a tenant/global key and assume company-specific resolution works identically.

For `gemini-3.5-flash-lite`, **Gemini thinking** offers **Provider default** or
**Medium (billed thinking)** in the creation wizard's Behavior step and in an
existing agent's **Config > Edit** screen. Provider default is unchanged for
existing agents and agents without an explicit choice. Medium applies to the
configured agent's supported Gemini reasoning, not every workspace or generic chat.
Thinking uses billed output tokens; budget for the additional token use and
response time within your existing limits. It is not an answer-accuracy guarantee.
Choose **Provider default** and save to remove the opt-in. API clients use
`llm.thinking_level="medium"`, or `null` to restore the default; incompatible
model/profile combinations are rejected. No global feature flag is needed.

## Rotate without losing the operating context

Record which agents and environments use the credential. Use the supported rotation action, test a bounded run, and monitor failures. Do not remove an old provider account before confirming all scheduled and integration-driven consumers have switched. Never log raw keys in a rotation report.

## Diagnose model failures

| Symptom | Check | Safe next action |
| --- | --- | --- |
| Credential not found | Tenant/company, credential kind and provider | Configure the correct authorized binding |
| Unauthorized/model not found | Key scope, endpoint and exact model ID | Correct provider configuration; do not paste keys into chat |
| Rate-limit or overload | Provider quota, concurrency, current incident | Retry with bounded backoff or approved routing |
| Knowledge search fails | Embedding service and indexed data | Test retrieval separately from LLM generation |
| Unexpected model/cost | Routing mode and actual run metadata | Adjust reviewed configuration and repeat the same task |

Jev/System One is an optional advisory integration with separate TypeSafe access and evaluation requirements. A waitlist enrollment does not supply an API key or activate production routing. Keep the ordinary model path usable without it.
## Default reasoning and your own Google account

New agents default to `gemini-3.5-flash-lite`. Existing agents keep an explicitly
selected model. Administrators can choose another model and fallback in agent/AI
settings; operators can override unset defaults with `AGENTICORG_LLM_PRIMARY`
and `AGENTICORG_LLM_FALLBACK`.

Create or select a Google AI Studio project, enable the provider account and
appropriate billing, generate an API key, and save it in the protected **AI
credentials** Gemini form. Never paste the key into chat or an agent prompt.
Choose the correct company or tenant scope and test a small reasoning request.
Provider availability, quota and pricing depend on your account. This model does
not produce audio: configure STT/TTS separately for voice workflows. See the
[channel workflow guide](/docs/channel-workflows) for account setup and tests.

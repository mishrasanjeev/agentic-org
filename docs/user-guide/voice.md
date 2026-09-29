## Shipped voice path

The implemented voice runtime is a signed Twilio webhook loop using provider-managed speech recognition and text-to-speech. It uses the agent's instructions, tools and tenant boundaries. Selecting another voice/STT/TTS option in configuration does not prove that its runtime worker is shipped or ready.

The administrator accesses **Voice Agents / Voice Setup**. You need a Twilio account, approved number, credentials, active mapped agent, reachable callback URLs and a budget owner. Phone calls may incur charges.

## Configure and validate

1. Select the target agent and Twilio provider.
2. Enter the account SID, auth token and provider number through the protected form.
3. Test provider credentials.
4. Select **Provider speech recognition** and **Provider text-to-speech**.
5. Choose the call language and save.
6. Configure the displayed inbound/status callback URLs in the provider console where provisioning is not automated.
7. Check runtime health for the exact agent, not merely credential storage.

```flow
Caller speaks | Twilio collects a speech turn for the configured number.
Signed callback | AgenticOrg verifies the signature and tenant/agent binding.
Bounded agent turn | Approved model/tools prepare a safe text response.
Spoken reply | Escaped TwiML returns speech and the next input prompt.
Call evidence | Status and encrypted bounded text are recorded; history is masked.
```

## Run a safe end-to-end test

Agree a test phone destination and time window with the owner. Use a synthetic script: ask a supported question, stay silent, ask an ambiguous question and ask for a prohibited account/payment action. Verify recognition, spoken reply, escalation wording, actual provider call status and masked call history.

Never place a paid outbound call to an unapproved number. A local signed-webhook simulation tests software behavior but not the PSTN connection, acoustic quality or provider routing. Both forms of evidence have different purposes.

## Privacy and service boundaries

AgenticOrg does not store call audio in this path or expose full transcripts in the call-history API. Bounded conversation text is encrypted. The provider's recording, retention and regional settings are separate and must be reviewed with your data owner.

For BFSI, general policy answers and complaint-routing assistance are appropriate starting points. Voice identity, balance disclosure, account access, transfer instructions and card actions need institution-approved authentication and integrations. A caller saying an account number is not identity verification.

## Troubleshoot

No callback: verify provider URLs and routing. Signature rejection: inspect canonical URL/signing configuration; do not disable validation. Provider-ready but no answer: test model/agent activity and tool permissions. Bad transcription: inspect language, microphone/network and a representative test set. Business failure: return a safe spoken error, never claim the action succeeded.

Next: [BFSI customer service](/docs/bfsi-customer-service), [Troubleshooting](/docs/troubleshooting).

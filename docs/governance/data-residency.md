# Data residency

A regulated institution needs every model call, retrieval, embedding, speech
transcription and third-party tool call to stay inside its jurisdiction, and
needs its data kept out of any vendor's training. The platform cannot know where
a vendor hosts an endpoint or what the vendor has promised in a contract. It
can refuse to use a provider until an administrator has recorded both facts,
and report what it is configured to do. That is residency enforcement.

## The region

Each tenant has a `data_region` in its governance config (`IN`, `EU` or `US`,
`PUT /api/v1/governance/config`). The deployment has a platform default
(`AGENTICORG_DATA_REGION`) used where no tenant is in context, and a
`AGENTICORG_STORAGE_REGION` naming the cloud region of its object storage.

## Turning enforcement on

The control ships off. Two switches turn it on:

- `AGENTICORG_RESIDENCY_ENFORCE=true` for the whole deployment, or
- the authority flag `residency.enforce` for one tenant, set by a platform operator:

```bash
python scripts/authority_flags.py set residency.enforce --tenant <tenant uuid> --operator <name>
```

With the control off nothing is read and nothing changes.

## Provider attestations

An attestation is an administrator's record, per provider and data region, that

- processing for that provider stays inside the region (`in_region`), and
- the provider has committed in writing not to train on the institution's data
  (`no_training`),

with an `evidence_ref` pointing at the contract clause or order form. Both must be
true for the provider to be usable in that region. Attestations can expire and can
be revoked; every change writes a signed audit row (`residency_attestation.set`,
`residency_attestation.revoked`).

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/v1/residency/status` | Region, enforcement state, storage and disaster-recovery conformance, active attestations. |
| `GET` | `/api/v1/residency/attestations` | Active attestations (`?include_revoked=true` for history). |
| `POST` | `/api/v1/residency/attestations` | Record one. |
| `POST` | `/api/v1/residency/attestations/{id}/revoke` | Revoke one. |

```json
{
  "provider": "gemini",
  "data_region": "IN",
  "in_region": true,
  "no_training": true,
  "evidence_ref": "Master agreement 2026-07, clause 7.2 and order form OF-18",
  "expires_at": "2027-06-30T00:00:00Z"
}
```

Provider ids are the ones the credential resolver uses: `gemini`, `openai`,
`anthropic`, `azure_openai`, `openai_compatible`, `voyage`, `cohere`, `ragflow`,
`stt_deepgram`, `stt_azure`, `tts_elevenlabs`, `tts_azure`, plus `composio` for
the third-party tool hub. Providers that run inside the deployment (`ollama`,
`vllm`, local embeddings, local speech engines) need no attestation: their
region is the deployment's.

## What is enforced

| Point | Behaviour with enforcement on |
|---|---|
| AI credential resolver (`core/ai_providers/resolver.py`) | Every LLM, embedding, retrieval and speech credential is refused unless the provider is attested for the tenant's region (`ResidencyBlocked`, code `E4006`). The model router treats the refusal as final: no fallback to another model. |
| Managed retrieval service (`api/v1/knowledge.py`) | Uploads and searches go to the external retrieval service only when `ragflow` is attested; otherwise the native in-database pipeline is used. |
| Third-party tool hub (`connectors/composio/adapter.py`) | Tool execution is refused unless `composio` is attested. |
| Tracing export (`observability/trace_redaction.py`) | With deployment-wide enforcement, external tracing stays off and its environment switches are cleared. With tenant-scoped enforcement, the payload of a run whose tenant enforces residency (or whose enforcement has not been read yet) is withheld, as is any payload that names no tenant while some tenant in the process enforces; only `hidden: residency` and the tenant id are exported. |

Without a tenant in context (platform probes, module-level configuration) the
platform default region applies and no attestation exists, so an external
provider is refused.

## Fail-closed rules

- In a strict runtime, a region or attestation list that cannot be read blocks
  the provider. In a relaxed runtime it is allowed and logged.
- Refusals are counted in `agenticorg_residency_refusals_total{reason}`.

## The compliance report

`GET /api/v1/compliance/evidence-package` gains a `data_residency` section
(control `RES-1`): the tenant's region, whether enforcement is on, the storage
region and whether it lies inside the data region, the tenancy profile
(`AGENTICORG_TENANCY_PROFILE`: `shared` or `dedicated`), the disaster-recovery
profile (`AGENTICORG_DR_STANDBY_REGION`, `AGENTICORG_DR_LAST_DRILL_AT`, with the
standby's conformance) and the active attestations for the region. The report
states what is configured; it does not verify the cloud provider's behaviour.

## What this does not do

- It does not move data. A provider outside the region is refused, not rerouted.
- It does not verify a vendor's hosting. The attestation is the administrator's
  record of the contract; the audit trail shows who recorded it and when.
- Alert notifications (budget email, chat and webhook) are not gated; they carry
  spend figures and identifiers, not customer content.

# In-country deployment reference

The infrastructure items in the baseline belong to the hosting platform, not to application code. This
reference maps each one to a deployment control that any major cloud or sovereign-cloud provider can
supply, the verification a reviewer performs, and the platform setting or report that consumes it. The
compliance report records the outcome of each verification in its infrastructure attestation section.

| ID | Capability | Deployment control | Verification |
|---|---|---|---|
| INF-01 | Multi-zone architecture | Select an in-country region with at least three zones; pin all services to it. | List the zones of the configured region and confirm count. |
| INF-02 | Native disaster recovery | Multi-zone database and cache; warm standby in a second in-country region with replication and tested failover. | Run the DR runbook; record RTO and RPO achieved. |
| INF-03 | AI accelerators | GPU or inference-optimised node pools in-country for self-hosted models; the model gateway refuses providers outside the region when residency enforcement is on. | List accelerator quotas in the region; run the local-inference smoke test. |
| INF-04 | Managed Kubernetes | Managed cluster with automatic upgrades, node auto-scaling and health checks; Helm values in the repository. | Describe the cluster; confirm auto-upgrade and autoscaler settings. |
| INF-05 | Managed serverless containers | API and UI as serverless container services with scale-to-zero where allowed. | Describe the services; confirm min and max instances. |
| SEC-01 | Hardware security module | Key-encryption keys held in a single-tenant HSM at FIPS 140-2 Level 2 or above; the platform's envelope encryption references the HSM key. | Show the key's protection level and tenancy. |
| SEC-02 | Key management and BYOK | Customer-managed key-encryption keys with import, rotation and per-tenant bring-your-own-key. | Rotate the key; run the rewrap job; confirm the compliance report. |
| SEC-03 | Secrets management | Runtime secrets in the managed secret manager with rotation schedules; database and certificate rotation automated. | List secrets with rotation periods; run the rotation workflow. |
| SEC-04 | Web application firewall | Managed WAF with OWASP rules and rate-based rules in front of the ingress. | Show the WAF policy attached to the ingress. |
| SEC-05 | Managed DDoS protection | Managed layer 3 and 4 DDoS protection on the public endpoints. | Show the protection plan on each public address. |
| SEC-06 | Security posture management | Runtime image and instance scanning in the provider's posture service, plus the repository's CI scans. | Export the posture findings for the project. |
| SEC-07 | Threat detection | Provider threat detection over control-plane logs and network flows; alerts routed to the SIEM. | Trigger a test finding and confirm it reaches the SIEM. |
| SEC-08 | SIEM and security lake | Audit and security logs exported on an open schema to the institution's SIEM. | Query the SIEM for a known audit event. |
| SEC-09 | Identity and access management | Provider IAM with roles, temporary credentials and enforced multi-factor authentication; platform RBAC and SSO. | List roles and the MFA policy. |
| DATA-01 | Scalable object storage | Regional object storage with multi-zone replication; versioning on. | Show the bucket location, replication and durability class. |
| DATA-02 | Serverless SQL query engine | Serverless query service over object storage for analytics exports. | Run a query over an exported audit dataset. |
| DATA-03 | Managed lakehouse | Managed open table format with compaction and ACID transactions for analytical exports. | Create a table from an export; confirm transactions. |
| DATA-04 | Managed streaming | Managed streaming service for event export; the platform publishes audit and run events. | Publish a test event; consume it. |
| DATA-05 | Managed serverless ETL | Managed ETL for scheduled transformations of exports. | Run a scheduled job. |
| DATA-06 | Data catalogue and lineage | Provider data catalogue registering the platform's datasets; platform lineage feeds it. | Find the audit dataset in the catalogue with its lineage. |
| DATA-07 | Managed search engine | Managed search service for logs and dashboards. | Search a known log line. |
| DATA-08 | Managed relational databases | Managed PostgreSQL with multi-zone high availability and automated backups. | Show availability type, backup schedule and point-in-time recovery. |
| AIINF-13 | Parameter-efficient fine-tuning | Managed parameter-efficient fine-tuning in-country, gated by the evaluation framework and model governance before any model enters the registry. | Run a tuning job on synthetic data; confirm the registry entry and evaluation gate. |
| NET-01 | Private service endpoints | Private connectivity between the platform and institution systems; no public ingress for integration traffic. | Confirm the private endpoint and the absence of public routes. |
| NET-02 | Enterprise API gateway | Managed API gateway with throttling, API keys, WebSocket support, logging and request transformation in front of the platform APIs. | Call through the gateway; confirm throttling and logging. |
| NET-03 | Managed secure file transfer | Managed SFTP into object storage for batch ingestion wired to the ingestion jobs. | Upload a test file; confirm ingestion. |
| NET-04 | Managed service mesh | Mesh with mutual TLS and telemetry between services. | Show mTLS mode and a service-to-service trace. |
| OPS-03 | Infrastructure as code | All infrastructure declared in Terraform with drift detection in CI. | Run the plan; confirm no drift. |
| OPS-05 | Fault injection and resilience testing | Managed fault-injection experiments against the stack with reports. | Run an experiment; attach the report. |
| OPS-06 | Managed CI/CD | Managed pipelines in-country or the repository's pipelines running on in-country runners. | Show a pipeline run and its runner location. |
| OPS-07 | Immutable backup (WORM) | Backups in object storage with retention lock that administrators cannot shorten. | Attempt a delete inside the retention window; confirm refusal. |
| OPS-08 | Confidential compute | Confidential node pools or instances for model inference and agent sessions where available. | Show the confidential setting on the pool. |
| GOV-01 | Published per-service SLAs | Provider SLAs and the platform's service-level document with credits. | Attach the SLA documents. |
| GOV-02 | Proven in-country track record | Provider operating history in-country. | Attach the provider's published records. |
| GOV-03 | 24x7 enterprise support | Enterprise support plan with in-country account management. | Attach the support plan. |
| FE-08 | Governed API exposure | Platform APIs published through the institution's API gateway with the platform's scoped keys and SDKs. | Call a scoped API through the gateway. |

## Recording verifications

The compliance evidence package (`GET /api/v1/compliance/evidence-package`) carries an
`infrastructure_controls` section listing every item above with the status an operator
recorded: `verified`, `not_applicable`, or `not_verified` when nothing is recorded. The
operator keeps the record in a JSON file named by `AGENTICORG_INFRASTRUCTURE_ATTESTATIONS_FILE`:

```json
{
  "attestations": [
    {"id": "INF-01", "status": "verified", "verified_at": "2026-09-30", "verified_by": "platform-ops", "evidence_ref": "change CHG-1182"},
    {"id": "DATA-02", "status": "not_applicable", "evidence_ref": "no analytics exports"}
  ]
}
```

The report states what was recorded; it does not verify the hosting platform itself. An
unreadable or invalid file makes the section `unavailable` with a reason code (`unreadable`
or `invalid`) and the error type, never a silent pass; the configured path and the error
text stay in the operator log, not in the package (`source` is the label `attestations_file`).

## Platform settings that consume these controls

- `data_region` and `storage_region` select the region; with residency enforcement on the platform refuses providers and storage outside it.
- The envelope-encryption key resource points at the HSM-backed key (SEC-01, SEC-02).
- The audit and event exports target the streaming and SIEM services (SEC-08, DATA-04).
- The DR profile records the standby region and the last drill (INF-02).

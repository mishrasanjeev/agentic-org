# Insights Phase 3 acceptance (evidence and reporting)

The PRD's Phase 3 gate (docs/INSIGHTS_DASHBOARD_PRD.md, Phased delivery plan): an auditor signs a period evidence
pack from a staging tenant, and three usability tasks that need Phase 3 features are completed unaided within three
minutes each.

## Before the session

1. Use a staging tenant with at least a week of runs, one failed run with a trace, approvals (one decided under a
   delegation) and audit rows. Audit chain sealing on is preferred (`AGENTICORG_AUDIT_CHAIN_ENABLED=true`) so the
   pack's verification covers the chain.
2. Set `AGENTICORG_INSIGHTS_EXPORT_BUCKET` on the API, worker and beat (docs/runbooks/insights-exports.md).
3. Participants: an auditor (role `auditor`), a CFO (role `cfo`) and an operations or engineering user (role `coo`
   or `developer`).

## The gate

- **Auditor signs a period evidence pack.** The auditor opens Insights (lands on Compliance), asks for the evidence
  pack for last month, opens it from Downloads, checks `verification.json` and the file checksums against
  `manifest.json` (`sha256sum`), then signs it with a statement. Pass: the Downloads row shows the signature and the
  audit log has the `insights.evidence_pack.signed` row.

## Usability tasks (unaided, three minutes each)

| # | Task as read to the participant | Pass when they | Where |
|---|---|---|---|
| 1 | "Find out why this run failed." (give the run's agent and time) | Name the failing step and its error from the "Why this run failed" panel or the waterfall. | Runs explorer, run page |
| 2 | "Download the finance pack for last month." | Ask for the finance pack on the Finance view and open the ZIP from Downloads. | Finance view, Downloads |
| 3 | "Get the Finance view emailed to you every week." | Use "Schedule weekly" (or the Report Scheduler) and save a weekly Insights view schedule. | Finance view, Report Scheduler |

Record the time taken and any help given. The gate passes when the auditor signs a pack and every participant
completes their tasks unaided.

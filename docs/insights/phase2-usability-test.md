# Insights Phase 2 usability test

The Phase 2 gate in the Insights PRD (`docs/INSIGHTS_DASHBOARD_PRD.md`, "Phased delivery plan and acceptance
criteria"): a moderated test in which one participant per role finds answers in Insights unaided.

## Pass rule

- Five participants, one each signed in as **admin**, **CFO**, **CHRO**, **CMO** and **COO**.
- Seven tasks each. A task passes when the participant completes it **unaided within 3 minutes**.
- The gate passes when **every participant completes at least 6 of the 7 tasks**.

Two tasks change something that only some roles may change: confirming a savings baseline (a tenant administrator)
and revoking a learned rule (an administrator, or the agent's owner). For a participant whose role may not make the
change, the task passes when they reach the control and can say, from the page, who can make it. Agree this reading
with the product owner before the sessions; if it is not accepted, those two tasks are run with the admin participant
only and the others are scored on the remaining five.

## Before the sessions

1. Use a staging tenant with Insights on and recorded runs in every domain for the last 60 days. Demo-seeded runs do
   not count towards savings, so the tenant needs measured runs.
2. Make sure the data supports each task:
   - several agents with spend this month, one clearly the most expensive;
   - approvals requested last month, some decided;
   - one agent whose spend rose this period against the last, mainly from more runs (a volume driver);
   - at least one shadow agent that passes its checks and one that does not;
   - at least one active agent with no run for 30 days or more;
   - at least one learned rule on an agent in each domain head's domain;
   - money-saved baselines and rates seeded and **not yet confirmed**.
3. Create the five participant accounts. Each sees only their own domain.
4. Reset the state between participants: unconfirm the baselines, and restore any revoked rule and promoted agent.
5. Record the screen (with consent). Start each participant on the Insights page (`/dashboard/insights`).

## Moderator script

> "We are testing the product, not you. Please think aloud. I will read each task and will not help; if you are stuck
> after three minutes we move on. There are no wrong answers."

Read each task as written. Start the timer when you finish reading it, and stop it when the participant states the
answer or completes the action. Do not point at the screen.

## Tasks

| # | Task (read aloud) | Done when | Where it is (for the moderator only) |
| --- | --- | --- | --- |
| 1 | "Which agent has cost the most this month?" | Names the agent with the highest spend in the current month (admin and CFO: across the tenant; the others: in their domain). | Explore, Agents or Cost and drivers; choose this month and sort by spend. |
| 2 | "Download last month's approvals as an Excel file." | The XLSX file for last month's approvals is downloaded. | Explore, Approvals; set a custom range for last month, then Export, XLSX. |
| 3 | "Spend went up recently for one agent. Tell me why." | Names the agent and the driver (more runs, higher prices, or a change in models or usage types). | Explore, Cost and drivers; sort by change; read the Driver and the effect columns. |
| 4 | "Promote the shadow agent that is ready, or tell me why it is not ready." | Promotes an eligible agent, or reads out the reason an agent is not eligible. | Explore, Agents; filter Status shadow; the Eligible to promote and Promotion check columns; the agent page to promote. |
| 5 | "Which agents have not run for 30 days?" | Lists the idle agents. | Explore, Agents; Status active and Idle days at least 30. |
| 6 | "Remove a learned rule from one of your agents." | Revokes a rule, or (for a role that may not) shows the control and says who can. | Explore, Quality and learning; Revoke on a rule. |
| 7 | "Confirm the savings assumption for invoice processing." | Confirms the AP baseline, or (for a role that may not) says a tenant administrator confirms it. | Money saved; the baselines table, ap processor, Confirm (or Confirm all used). |

## Recording sheet

Copy one row per participant and task.

| Participant role | Task | Completed (yes / no / partial) | Time (mm:ss) | Unaided (yes / no) | Where they looked first | Notes (quotes, wrong turns) |
| --- | --- | --- | --- | --- | --- | --- |
| admin | 1 | | | | | |

## After the sessions

- Count the completed tasks per participant and apply the pass rule.
- List each task that two or more participants failed or took over two minutes on, with the place they looked first.
  These are the fixes before the gate is re-run.
- Note any number a participant did not trust, and why (for example "I don't know where this comes from"). The
  definition, the data quality badge and the export's Definitions sheet should answer it.
- File the results with the date, the staging tenant and the build (`/api/v1/health` commit).

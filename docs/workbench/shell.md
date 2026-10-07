# Workbenches: role-shaped consoles

With `AGENTICORG_WORKBENCH_V2_ENABLED` on, `GET /workbench` lists the consoles a person works from
and the UI shows them at `/dashboard/workbench` (`core/workbench/`, `ui/src/pages/Workbench.tsx`).
Off, `GET /workbench` answers `enabled: false` with no workbenches and every other workbench route
is not found; nothing else changes, because the shell is a view over pages that already exist.

## The catalogue

`core/workbench/definitions.py` fixes four workbenches, each a set of tabs:

| Workbench | Tabs | Held by default |
| --- | --- | --- |
| Review officer | Approvals, Documents, Content drafts, Governed cases | admin, cfo, coo, domain_lead |
| Relationship manager | Conversations, Knowledge base, Agents, Governed cases | admin, cmo, domain_lead |
| Investigator | Documents, Governed cases, Audit trail, Run timelines | admin, auditor, analyst |
| Supervisor | Live conversations, Approvals, Guardrails, Costs | admin, coo |

A tab names the page it opens, the counter behind its badge, the actions it offers and, where the
page is restricted, the roles that may see it. A tab marked sensitive (the audit trail, run
timelines, live conversations with takeover, costs) is shown only to the roles it names, whatever
workbench the caller holds. `GET /workbench/catalogue` (administrators) returns all of this.

## Who holds a workbench

`core/workbench/access.py` decides: an administrator holds every workbench; another role holds the
workbenches that name it by default, plus any an administrator assigned
(`PUT /workbench/assignments/{user_id}` with the list of workbench names; an empty list removes
them; `GET /workbench/assignments` lists them by user; `workbench_assignments`, tenant-scoped under
row-level security). Within a held workbench a person sees the tabs their role may see. The shell
never authorises on its own: every page behind a tab checks the caller as it always did, so a tab
that is not listed is not reachable by typing its path either.

## Counts

`GET /workbench/{name}/summary` returns the workbench with the caller's tabs and the number of
items waiting behind each: approvals pending in the human-in-the-loop queue, documents in review,
content drafts pending approval, governed cases awaiting a decision, conversations active or
escalated. A tab without a counter, or a
store that cannot be read, reports `null` rather than zero, and `waiting` sums only the counts that
were read.

## The review queue

`GET /workbench/queue` is one list of everything waiting for a person: approvals pending in the
human-in-the-loop queue (not yet expired), documents in review, content drafts pending approval and
governed cases awaiting a decision, each normalised to one shape (kind, title, summary, priority,
age, due date where there is one, the page that shows it and the actions it allows) and ordered by
priority then age. A caller sees the kinds a held workbench shows; `kind` narrows the list.
`GET /workbench/queue/{kind}/{id}` returns the item in full with the fields a reviewer may edit
before deciding.

`POST /workbench/queue/{kind}/{id}/decide` takes the decision, notes and a list of edits, applies
the edits first and then decides through the store that owns the item, so that store's rules apply
unchanged: a draft's title and text fields are edited with the originals kept (`content_drafts.edits`)
and decided under maker-checker with the administrator scope; a document's extracted fields are
corrected with the original beside and decided through the review store; an approval's amendments
are recorded on the item (`context.review_edits`) and summarised in the decision notes, which a
resumed run receives, and the decision goes through the approvals route's own function (role
hierarchy, delegation, expiry, policy steps); a governed case is decided on its own page, and the
queue says so. The review officer's and the supervisor's workbenches show the queue as a tab, with
the sum of the four counters behind it.

## The shell

The index lists the held workbenches with how each is held (by role or by assignment). A workbench
shows its tabs with their counts; a tab opens the page it names, with the count and the actions the
tab offers beside the link. Tabs that live in the shell render inside it: the review officer's
content drafts tab lists the drafts pending approval and records approve or reject through the
content drafts API, which refuses a decision from the draft's own author or from a role without
the administrator scope.

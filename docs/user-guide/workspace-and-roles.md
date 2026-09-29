## Organization, company and user are different things

The **organization/tenant** is the security boundary for your workspace. A **company** is a business context within that workspace, particularly useful for partner or multi-company operations. A **user** receives a role and permissions. Selecting a company does not grant access to another tenant.

Many confusing errors come from doing the right action in the wrong company or with a role that cannot perform it. Always check company context before interpreting connector readiness, agent lists or model credentials.

## Set up the organization

The onboarding wizard includes organization details, industry and size, team invitations, system connection and a completion checklist. Choose **Finance & Banking** where appropriate; selecting an industry does not provision core banking, KYC providers or a compliance certification.

1. Confirm you are setting up a new organization rather than duplicating an existing one.
2. Enter reviewed organization information.
3. Invite named colleagues using the least-powerful suitable role.
4. Review connector setup separately; finish only the connections you are ready to own.
5. Assign a platform administrator and a backup owner.

## Understand role-based navigation

The sidebar deliberately differs by role. The backend, not a visible button, decides whether the operation is authorized. Current common roles include administrator, finance/CFO, HR/CHRO, marketing/CMO, operations/COO, auditor, merchant and domain-lead contexts.

| Task | Typical owner | Important boundary |
| --- | --- | --- |
| Organization settings, credentials and billing | Administrator | Do not distribute an admin login to operators |
| Domain agents and workflows | Authorized domain lead | Tools and data remain separately scoped |
| Merchant commerce runtime | Administrator or merchant | Merchant binding and publishing settings still apply |
| Evidence and audit review | Authorized auditor/reviewer | Read access is not decision or execution permission |
| Human case decision | Named authorized approver | Machine credentials cannot impersonate a person |

This is a responsibility guide, not an exhaustive permission table. Exact scopes are deployment- and role-dependent. When a link is absent or an API returns `403`, have the administrator review your assigned role and required scope.

## Multi-company operating checklist

Before a run, check the selected company, agent ownership, model credential binding, connector binding and input references. A tenant-wide connector can be available under different rules from a company-specific connector; a healthy global connector does not prove that your company can use it.

Do not copy a successful company's connector ID into another company's task. Have the owner configure the correct binding and re-test with that company selected. Verify negative access tests as well as successful ones.

## Team changes and account recovery

During onboarding, the **Send Invites** action creates the entered invitations.
The CFO/CHRO/CMO/COO labels map to domain-lead invitations for their respective
domains. Check any per-recipient failure before retrying; successful recipients
are cleared from the form rather than being resent.

In **Settings > User Management**, follow the entry points for company role
management. Per-company mappings live in **Companies > company detail > Settings
> Company Role Mapping**. That section is not a complete tenant user-directory
or SSO-provisioning console. Have your administrator use the supported invitation
and identity process for your deployment, not an invented bulk member editor.

Reset forgotten passwords through the sign-in flow; never ask a colleague for theirs.
When a person leaves, review access, API keys, connector ownership, workflow
schedules and outstanding approvals, not just their login. Confirm revocation
with the actual identity/grant issuer as well as company role mappings.

SSO, SCIM and enterprise identity capabilities depend on plan and deployment configuration. Agree the source of truth with IT before relying on automated offboarding. [Security and data](/docs/security-and-data) and [Adoption checklist](/docs/adoption-checklist) cover ownership and review.

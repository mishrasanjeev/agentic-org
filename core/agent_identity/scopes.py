# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""The scope each agent action needs, in the form both the attestation and the grant carry.

An action runs only when its scope is in the attestation's allowed scopes *and* in the agent's
Grantex grant: the narrower of the two sets. Case agents' provider calls use the per-tool scopes
the case agents are registered with (``tool:<provider>:read:<tool>``).
"""

from __future__ import annotations

#: Desk agent roles and the tenant agent type each one is registered as.
OUTREACH_ROLE = "onboarding_outreach"
CONVERSATION_ROLE = "onboarding_conversation"
CASE_ROLES = ("business_underwriter", "screening_disposition")
DESK_ROLES = (OUTREACH_ROLE, CONVERSATION_ROLE)
ALL_ROLES = (*DESK_ROLES, *CASE_ROLES)

OUTREACH_SEND = "agenticorg:onboarding_desk:send:outreach"
SMS_REPLY = "agenticorg:onboarding_desk:reply:sms"
ENROLMENT = "agenticorg:onboarding_desk:enrol:agent"

#: The scope each desk role acts under.
DESK_ROLE_SCOPES = {OUTREACH_ROLE: OUTREACH_SEND, CONVERSATION_ROLE: SMS_REPLY}


def provider_tool_scope(connector: str, tool: str) -> str:
    return f"tool:{connector}:read:{tool}"

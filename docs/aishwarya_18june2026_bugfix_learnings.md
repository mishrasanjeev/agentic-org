# Aishwarya 18 June 2026 Bugfix Learnings

Permanent rules from the TC_001-TC_005 reopen analysis:

1. Connector health checks must use the same upstream API path as the real tools. A separate "lightweight" path is not acceptable unless the provider documents it and a regression test pins it. Google Ads health must use `googleAds:searchStream`, not `/customers/{id}`.
2. Connector errors must keep provider-specific remediation. A generic 403 message is not enough when the product already knows the provider, endpoint, required scope, and permission model.
3. UI success messages must be driven by persisted backend state or an explicit backend metric result. Do not report "updated count and accuracy" from a successful HTTP 200 alone.
4. OAuth provider support and connector runtime support must come from the same registry contract. If a connector has a Reconnect action, the OAuth registry must support that provider or the action must be hidden.
5. Readiness contracts must validate capabilities, not just stored scope strings. HubSpot private-app and legacy OAuth configs may prove CRM read access through healthy connector tests and registered tools even when no OAuth `scope` field is persisted.
6. Every reopen fix must add at least one backend regression and one UI or Playwright regression when user-visible behavior changed.
7. Production credential retests must not print secrets. Report only pass/fail, HTTP status, and whether actionable guidance was present.

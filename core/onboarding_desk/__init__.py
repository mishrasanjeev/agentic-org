# SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
"""Merchant onboarding desk: turning a partner's opted-in leads into verified, reviewed merchants.

Off by default (``AGENTICORG_ONBOARDING_DESK_ENABLED``). This package holds the
foundations every later step relies on:

- ``contacts``: normalising, masking and hashing a lead's email and phone,
  and recognising opt-out keywords;
- ``rules``: whether a lead may be contacted on a channel now, and why not,
  with the contact-hour and attempt floors for the United States and Canada;
- ``leads``: validating a lead and its consents from an API call, a signed
  webhook or a CSV import;
- ``store``: the tenant-scoped records (leads with encrypted contact details,
  per-channel consents, suppressions, lead sources, the partner policy and
  the contact-attempt log).

Outreach, the onboarding conversation and the hand-off into a governed case
build on these. No step contacts anyone without a consent on record.
"""

"""Enterprise SSO — OIDC (all major IdPs) and SAML (planned).

OIDC covers: Okta, Azure AD / Entra ID, Google Workspace, Auth0,
OneLogin, Ping Identity, Keycloak — all big enterprise IdPs ship with
OIDC support. SAML is a follow-up for legacy on-prem IdPs.

See docs/adr/0004-sso-oidc-first.md for the decision record.
"""

from auth.sso.oidc import OIDCProvider as OIDCProvider
from auth.sso.provisioning import jit_provision_user as jit_provision_user

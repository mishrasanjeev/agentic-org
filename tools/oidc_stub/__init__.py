# SPDX-License-Identifier: Apache-2.0
"""OpenID Connect provider stub for the local development stack.

Discovery, JWKS, the authorization-code flow with PKCE, a token endpoint,
userinfo and step-up authentication (``acr_values`` / ``max_age`` producing
``acr`` / ``amr`` / ``auth_time``). Users and clients come from a JSON config
file. Refuses to start outside development and test runtimes. See
``docs/quickstart-local.md``.
"""

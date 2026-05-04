export const AUTH_TYPES = ["oauth2", "api_key", "basic", "bolt_bot_token", "certificate", "none"] as const;

export const AUTH_FIELD_HINTS: Record<string, string> = {
  oauth2: "Client ID and Client Secret; refresh token is created by the authorization flow",
  api_key: "API key or token",
  basic: "Username and password",
  bolt_bot_token: "Slack Bot User OAuth Token (xoxb-...)",
  certificate: "Certificate path or PEM content",
  none: "No authentication required",
};

export function authTypeLabel(authType: string): string {
  if (authType === "bolt_bot_token") return "Bot Token";
  if (authType === "api_key") return "API Key";
  if (authType === "oauth2") return "Client Secret";
  if (authType === "basic") return "Password";
  return "Auth Credential";
}

export function buildAuthConfig(authType: string, token: string): Record<string, string> {
  if (!token.trim()) return {};
  const t = token.trim();
  if (authType === "bolt_bot_token") return { bot_token: t };
  if (authType === "api_key") return { api_key: t };
  if (authType === "oauth2") return { client_secret: t };
  // TC_008 (Aishwarya 2026-04-23): basic auth is username + password,
  // but this single-token helper only had a slot for password. The
  // ConnectorDetail edit flow now uses buildBasicAuthConfig explicitly
  // to pass both. This helper is preserved for the single-field auth
  // types and keeps "password" as a back-compat slot for flows that
  // want to update the password without touching the username.
  if (authType === "basic") return { password: t };
  return { token: t };
}

/** Build an auth_config payload for basic auth (username + password). */
export function buildBasicAuthConfig(
  username: string,
  password: string,
): Record<string, string> {
  const out: Record<string, string> = {};
  const u = username.trim();
  const p = password.trim();
  if (u) out.username = u;
  if (p) out.password = p;
  return out;
}

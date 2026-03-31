export const AUTH_TYPES = ["oauth2", "api_key", "basic", "bolt_bot_token", "certificate", "none"] as const;

export const AUTH_FIELD_HINTS: Record<string, string> = {
  oauth2: "Client ID, Client Secret, Token URL",
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
  if (authType === "basic") return { password: t };
  return { token: t };
}

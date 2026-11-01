// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * Typed client for Insights BI access (api/v1/insights_bi.py): a tenant
 * administrator's read-only SQL logins for a BI tool on the read replica.
 *
 * The backend is the boundary: it answers 404 insights_bi_disabled while BI
 * access is off, 403 to anyone but an active human administrator and 409 at
 * the per-tenant limit. A created login's password is returned once; callers
 * show it and then drop it, and nothing here stores it.
 */
import api from "@/lib/api";

export interface BiLogin {
  role_name: string;
  created_at: string | null;
  created_by: string | null;
  revoked_at: string | null;
}

export interface BiConnection {
  host: string;
  port: number;
  database: string;
  sslmode: string;
  channel_binding: string;
  schema: string;
  views: string[];
}

export interface BiAccessList {
  logins: BiLogin[];
  connection: BiConnection;
  max_logins: number;
  active_logins: number;
}

export interface BiCreatedLogin extends BiConnection {
  role_name: string;
  /** Shown once; never kept after the administrator dismisses it. */
  password: string;
  connection_string: string;
  created_at: string | null;
}

const BASE = "/insights/bi-access";

function text(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function nullableText(value: unknown): string | null {
  return typeof value === "string" && value !== "" ? value : null;
}

function normaliseConnection(raw: Record<string, unknown>): BiConnection {
  return {
    host: text(raw.host),
    port: typeof raw.port === "number" ? raw.port : 5432,
    database: text(raw.database),
    sslmode: text(raw.sslmode) || "verify-ca",
    channel_binding: text(raw.channel_binding) || "require",
    schema: text(raw.schema) || "insights_bi",
    views: Array.isArray(raw.views) ? raw.views.filter((v): v is string => typeof v === "string") : [],
  };
}

export function normaliseList(data: unknown): BiAccessList {
  const raw = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
  const logins = (Array.isArray(raw.logins) ? raw.logins : [])
    .filter((l): l is Record<string, unknown> => Boolean(l) && typeof (l as { role_name?: unknown }).role_name === "string")
    .map((l) => ({
      role_name: l.role_name as string,
      created_at: nullableText(l.created_at),
      created_by: nullableText(l.created_by),
      revoked_at: nullableText(l.revoked_at),
    }));
  const connection = normaliseConnection(
    (raw.connection && typeof raw.connection === "object" ? raw.connection : {}) as Record<string, unknown>,
  );
  const max = typeof raw.max_logins === "number" ? raw.max_logins : 0;
  const active = typeof raw.active_logins === "number" ? raw.active_logins : logins.filter((l) => !l.revoked_at).length;
  return { logins, connection, max_logins: max, active_logins: active };
}

export const biAccessApi = {
  async list(): Promise<BiAccessList> {
    const { data } = await api.get(BASE);
    return normaliseList(data);
  },
  async create(): Promise<BiCreatedLogin> {
    const { data } = await api.post(BASE);
    const raw = (data && typeof data === "object" ? data : {}) as Record<string, unknown>;
    return {
      ...normaliseConnection(raw),
      role_name: text(raw.role_name),
      password: text(raw.password),
      connection_string: text(raw.connection_string),
      created_at: nullableText(raw.created_at),
    };
  },
  async revoke(roleName: string): Promise<void> {
    await api.delete(`${BASE}/${encodeURIComponent(roleName)}`);
  },
};

/** The API's error code for a refusal (``detail`` or ``detail.error``), if any. */
export function errorCode(error: unknown): string | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && typeof (detail as { error?: unknown }).error === "string") {
    return (detail as { error: string }).error;
  }
  return null;
}

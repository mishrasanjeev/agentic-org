/**
 * Role lists and ownership helpers shared by route guards, nav, and pages.
 *
 * Bug sheet 2026-09-14 rows 17-19, 22, 30, 52: non-admin roles create
 * personal agents and connectors. The backend (core/rbac.py,
 * core/ownership.py) is the authorization boundary; these helpers only
 * mirror it so the UI does not offer controls that always return 403.
 */

export const ADMIN_ROLE = "admin";

/** Roles that may open the agent fleet and create (personal) agents. */
export const AGENT_CREATOR_ROLES: readonly string[] = [
  "admin",
  "cfo",
  "chro",
  "cmo",
  "coo",
  "domain_lead",
  "developer",
];

/** Roles that may open the connectors pages and register personal connectors. */
export const CONNECTOR_ROLES: readonly string[] = [
  "admin",
  "cfo",
  "chro",
  "cmo",
  "coo",
  "domain_lead",
  "developer",
];

/** Roles that may open the approval queue (the backend scopes the items). */
export const APPROVAL_ROLES: readonly string[] = [
  "admin",
  "cfo",
  "chro",
  "cmo",
  "coo",
  "domain_lead",
  "developer",
];

/** Agent domains offered by the create and edit forms. */
export const AGENT_DOMAINS: readonly string[] = ["finance", "hr", "marketing", "ops", "backoffice", "comms"];

/** Mirrors core/rbac.py ROLE_DOMAIN_MAP for the CxO roles. */
const FIXED_ROLE_DOMAINS: Record<string, string> = {
  cfo: "finance",
  chro: "hr",
  cmo: "marketing",
  coo: "ops",
};

/** Roles that may build agents in any domain. */
const ANY_DOMAIN_ROLES = new Set(["admin", "developer"]);

export interface RoleUser {
  role?: string | null;
  domain?: string | null;
  user_id?: string | null;
}

export function isAdminUser(user: RoleUser | null | undefined): boolean {
  return user?.role === ADMIN_ROLE;
}

/**
 * Domains the user may assign to an agent. Admins and developers get the
 * full list; CxO roles get their fixed domain; other domain roles get the
 * domain on their account (empty when none is assigned).
 */
export function agentDomainsForUser(
  user: RoleUser | null | undefined,
  allDomains: readonly string[] = AGENT_DOMAINS,
): string[] {
  const role = user?.role || "";
  if (ANY_DOMAIN_ROLES.has(role)) return [...allDomains];
  const fixed = FIXED_ROLE_DOMAINS[role];
  if (fixed) return [fixed];
  const own = (user?.domain || "").trim();
  return own && own !== "all" ? [own] : [];
}

/** True when the domain picker must be locked to the user's own domain(s). */
export function isDomainLocked(user: RoleUser | null | undefined): boolean {
  return !ANY_DOMAIN_ROLES.has(user?.role || "");
}

export interface OwnedAgent {
  visibility?: string | null;
  owner_user_id?: string | null;
}

/** Admins manage every agent; others manage only their own personal agents. */
export function canManageAgent(user: RoleUser | null | undefined, agent: OwnedAgent | null | undefined): boolean {
  if (!user || !agent) return false;
  if (isAdminUser(user)) return true;
  return (
    agent.visibility === "personal" &&
    !!agent.owner_user_id &&
    agent.owner_user_id === user.user_id
  );
}

export interface OwnedConnector {
  owner_user_id?: string | null;
}

/** Admins manage every connector; others manage only connectors they own. */
export function canManageConnector(
  user: RoleUser | null | undefined,
  connector: OwnedConnector | null | undefined,
): boolean {
  if (!user || !connector) return false;
  if (isAdminUser(user)) return true;
  return !!connector.owner_user_id && connector.owner_user_id === user.user_id;
}

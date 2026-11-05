// SPDX-License-Identifier: LicenseRef-Orchestrum-Personal-Use-1.0
/**
 * A retired dashboard page's URL: sends the person to where its features live now (an Insights view, Marketing
 * operations, Monitoring, the Enforce Audit page...). Bookmarks, emails and deep links keep working; with
 * ``keepQuery`` the old query string (a deep link's ``?panel=``) is carried over.
 */
import { Navigate, useLocation } from "react-router";
import { useAuth } from "@/contexts/AuthContext";

export function redirectTarget(to: string, search: string, keepQuery: boolean): string {
  if (!keepQuery || !search || search === "?") return to;
  const [path, query = ""] = to.split("?");
  const merged = new URLSearchParams(query);
  new URLSearchParams(search).forEach((value, key) => {
    if (!merged.has(key)) merged.append(key, value);
  });
  const text = merged.toString();
  return text ? `${path}?${text}` : path;
}

export default function LegacyRedirect({
  to,
  keepQuery = false,
}: {
  to: string | ((role: string) => string);
  keepQuery?: boolean;
}) {
  const { user } = useAuth();
  const { search } = useLocation();
  const target = typeof to === "function" ? to(user?.role ?? "") : to;
  return <Navigate to={redirectTarget(target, search, keepQuery)} replace />;
}

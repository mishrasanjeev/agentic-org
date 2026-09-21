// SPDX-License-Identifier: Apache-2.0
import { Link, useLocation } from "react-router";

const LINKS = [
  { to: "/dashboard/approvals", label: "Agent approvals", match: (path: string) => path === "/dashboard/approvals" },
  {
    to: "/dashboard/approvals/cases",
    label: "Governed cases",
    match: (path: string) => path.startsWith("/dashboard/approvals/cases"),
  },
];

/** Switches between the agent approval queue and the governed case screens. */
export default function ApprovalsSubnav() {
  const { pathname } = useLocation();
  return (
    <nav aria-label="Approvals" className="flex flex-wrap gap-2 border-b pb-2">
      {LINKS.map((link) => {
        const current = link.match(pathname.replace(/\/$/, ""));
        return (
          <Link
            key={link.to}
            to={link.to}
            aria-current={current ? "page" : undefined}
            className={`rounded-md px-3 py-1.5 text-sm font-medium ${
              current ? "bg-primary text-primary-foreground" : "text-foreground hover:bg-accent"
            }`}
          >
            {link.label}
          </Link>
        );
      })}
    </nav>
  );
}

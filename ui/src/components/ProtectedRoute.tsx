import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

interface ProtectedRouteProps {
  children: React.ReactNode;
  allowedRoles?: string[];
}

export default function ProtectedRoute({ children, allowedRoles }: ProtectedRouteProps) {
  const { isAuthenticated, user, isHydrating } = useAuth();
  const location = useLocation();

  // BUG-10 (Uday CA Firms 2026-05-02): on a hard refresh of a protected
  // route, the AuthProvider mounts with isAuthenticated=false until
  // /auth/me resolves. Without this gate, ProtectedRoute fires
  // <Navigate to="/login"> on the first paint and the browser bounces
  // the user before hydration ever finishes — even though the
  // agenticorg_session cookie is valid. Defer the redirect decision
  // until hydration completes.
  if (isHydrating) {
    return (
      <div
        role="status"
        aria-live="polite"
        className="flex min-h-screen items-center justify-center text-sm text-muted-foreground"
      >
        Loading session…
      </div>
    );
  }

  if (!isAuthenticated) return <Navigate to="/login" replace />;
  // Fail closed: if the session has a token but no hydrated user
  // object (e.g. after SSO flow where /auth/me failed), treat the
  // route as unauthorized. This prevents unhydrated sessions from
  // accessing role-gated routes.
  if (allowedRoles && !user) return <Navigate to="/login" replace />;
  if (allowedRoles && user && !allowedRoles.includes(user.role)) {
    // PR-C2: explicit 403 page instead of silently redirecting to
    // /dashboard/audit. The old redirect confused users ("why am I on
    // the audit page?") and masked RBAC behaviour. The access-denied
    // page tells them what was blocked and why.
    return (
      <Navigate
        to="/dashboard/access-denied"
        replace
        state={{
          attemptedPath: location.pathname,
          allowedRoles,
          currentRole: user.role,
        }}
      />
    );
  }
  return <>{children}</>;
}

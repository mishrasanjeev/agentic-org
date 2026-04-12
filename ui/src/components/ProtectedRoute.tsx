import { Navigate } from "react-router-dom";
import { useAuth } from "../contexts/AuthContext";

interface ProtectedRouteProps {
  children: React.ReactNode;
  allowedRoles?: string[];
}

export default function ProtectedRoute({ children, allowedRoles }: ProtectedRouteProps) {
  const { isAuthenticated, user } = useAuth();
  if (!isAuthenticated) return <Navigate to="/login" replace />;
  // Fail closed: if the session has a token but no hydrated user
  // object (e.g. after SSO flow where /auth/me failed), treat the
  // route as unauthorized. This prevents unhydrated sessions from
  // accessing role-gated routes.
  if (allowedRoles && !user) return <Navigate to="/login" replace />;
  if (allowedRoles && user && !allowedRoles.includes(user.role)) {
    return <Navigate to="/dashboard/audit" replace />;
  }
  return <>{children}</>;
}

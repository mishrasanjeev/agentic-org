import { useLocation, useNavigate } from "react-router-dom";
import { Helmet } from "react-helmet-async";
import { Card, CardHeader, CardTitle, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useAuth } from "@/contexts/AuthContext";

/**
 * AccessDenied — explicit 403 UX for role-gated routes.
 *
 * Before Enterprise Readiness PR-C2, `ProtectedRoute` silently redirected
 * unauthorized users to `/dashboard/audit`. That was confusing ("why am I
 * on the audit page?") and masked RBAC behavior. Now unauthorized access
 * lands here with the actual reason, the user's current role, and the
 * required role(s).
 *
 * The router passes the attempted path + allowed roles via location
 * state so the page can explain what was denied and why.
 */
interface AccessDeniedState {
  attemptedPath?: string;
  allowedRoles?: string[];
  currentRole?: string;
}

export default function AccessDenied() {
  const location = useLocation();
  const navigate = useNavigate();
  const { user } = useAuth();
  const state = (location.state as AccessDeniedState | null) || {};

  const attemptedPath = state.attemptedPath || "this page";
  const allowedRoles = state.allowedRoles || [];
  const currentRole = state.currentRole || user?.role || "unknown";

  return (
    <div className="min-h-[60vh] flex items-center justify-center p-6">
      <Helmet>
        <title>Access Denied — AgenticOrg</title>
      </Helmet>
      <Card className="max-w-xl w-full" data-testid="access-denied">
        <CardHeader>
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-full bg-red-100 text-red-600 flex items-center justify-center text-xl font-bold">
              403
            </div>
            <div>
              <CardTitle>Access denied</CardTitle>
              <p className="text-sm text-muted-foreground mt-1">
                Your current role can't view{" "}
                <code className="text-xs bg-muted px-1 rounded">{attemptedPath}</code>.
              </p>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-4 text-sm">
          <div className="grid grid-cols-2 gap-4">
            <div>
              <span className="text-xs text-muted-foreground uppercase tracking-wide">
                Your role
              </span>
              <p
                data-testid="access-denied-role"
                className="font-mono text-sm mt-1"
              >
                {currentRole}
              </p>
            </div>
            <div>
              <span className="text-xs text-muted-foreground uppercase tracking-wide">
                Required role{allowedRoles.length > 1 ? "s" : ""}
              </span>
              <p
                data-testid="access-denied-required"
                className="font-mono text-sm mt-1"
              >
                {allowedRoles.length > 0 ? allowedRoles.join(" | ") : "admin"}
              </p>
            </div>
          </div>

          <div className="rounded border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
            <p>
              RBAC is enforced server-side. Even if the UI could show this
              route, the API would reject writes from your role. If you
              need access, ask a tenant admin to grant the required role
              or a matching scope.
            </p>
          </div>

          <div className="flex items-center gap-2">
            <Button onClick={() => navigate("/dashboard")} variant="default">
              Back to dashboard
            </Button>
            <Button onClick={() => navigate(-1)} variant="outline">
              Go back
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

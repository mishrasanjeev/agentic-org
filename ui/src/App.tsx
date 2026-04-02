import { lazy, Suspense } from "react";
import { Routes, Route } from "react-router-dom";
import Analytics from "./components/Analytics";
import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";

/* ── Critical path: Landing page loaded eagerly ── */
import Landing from "./pages/Landing";
import NotFound from "./pages/NotFound";

/* ── Lazy import with auto-reload on chunk failure ── */
function lazyRetry(factory: () => Promise<{ default: React.ComponentType }>) {
  return lazy(() =>
    factory().catch(() => {
      const reloaded = sessionStorage.getItem("chunk_reload");
      if (!reloaded) {
        sessionStorage.setItem("chunk_reload", "1");
        window.location.reload();
      }
      sessionStorage.removeItem("chunk_reload");
      return factory();
    })
  );
}

/* ── Everything else lazy-loaded ── */
const Login = lazyRetry(() => import("./pages/Login"));
const Signup = lazyRetry(() => import("./pages/Signup"));
const InviteAccept = lazyRetry(() => import("./pages/InviteAccept"));
const ForgotPassword = lazyRetry(() => import("./pages/ForgotPassword"));
const ResetPassword = lazyRetry(() => import("./pages/ResetPassword"));
const Evals = lazyRetry(() => import("./pages/Evals"));
const Pricing = lazyRetry(() => import("./pages/Pricing"));
const Playground = lazyRetry(() => import("./pages/Playground"));
const Dashboard = lazyRetry(() => import("./pages/Dashboard"));
const Agents = lazyRetry(() => import("./pages/Agents"));
const AgentCreate = lazyRetry(() => import("./pages/AgentCreate"));
const SOPUpload = lazyRetry(() => import("./pages/SOPUpload"));
const Integrations = lazyRetry(() => import("./pages/Integrations"));
const AgentDetail = lazyRetry(() => import("./pages/AgentDetail"));
const Workflows = lazyRetry(() => import("./pages/Workflows"));
const WorkflowCreate = lazyRetry(() => import("./pages/WorkflowCreate"));
const WorkflowDetail = lazyRetry(() => import("./pages/WorkflowDetail"));
const WorkflowRun = lazyRetry(() => import("./pages/WorkflowRun"));
const Approvals = lazyRetry(() => import("./pages/Approvals"));
const Connectors = lazyRetry(() => import("./pages/Connectors"));
const ConnectorCreate = lazyRetry(() => import("./pages/ConnectorCreate"));
const ConnectorDetail = lazyRetry(() => import("./pages/ConnectorDetail"));
const Schemas = lazyRetry(() => import("./pages/Schemas"));
const Audit = lazyRetry(() => import("./pages/Audit"));
const Observatory = lazyRetry(() => import("./pages/Observatory"));
const Settings = lazyRetry(() => import("./pages/Settings"));
const Onboarding = lazyRetry(() => import("./pages/Onboarding"));
const SLAMonitor = lazyRetry(() => import("./pages/SLAMonitor"));
const PromptTemplates = lazyRetry(() => import("./pages/PromptTemplates"));
const SalesPipeline = lazyRetry(() => import("./pages/SalesPipeline"));
const OrgChart = lazyRetry(() => import("./pages/OrgChart"));

/* ── Role-specific dashboards ── */
const CFODashboard = lazyRetry(() => import("./pages/CFODashboard"));
const CMODashboard = lazyRetry(() => import("./pages/CMODashboard"));

/* ── ABM Dashboard ── */
const ABMDashboard = lazyRetry(() => import("./pages/ABMDashboard"));

/* ── Report Schedules ── */
const ReportScheduler = lazyRetry(() => import("./pages/ReportScheduler"));

/* ── Blog / Content pages ── */
const Blog = lazyRetry(() => import("./pages/blog/Blog"));
const BlogPost = lazyRetry(() => import("./pages/blog/BlogPost"));

/* ── Integration workflow page ── */
const IntegrationWorkflow = lazyRetry(() => import("./pages/IntegrationWorkflow"));

/* ── Google Ads landing pages ── */
const AdsLanding = lazyRetry(() => import("./pages/ads/AdsLanding"));

/* ── Resource / SEO content pages ── */
const Resources = lazyRetry(() => import("./pages/resources/Resources"));
const ResourcePage = lazyRetry(() => import("./pages/resources/ResourcePage"));

/* ── Loading fallback ── */
function PageLoader() {
  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="flex flex-col items-center gap-3">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 animate-pulse" />
        <p className="text-sm text-slate-400">Loading...</p>
      </div>
    </div>
  );
}

export default function App() {
  return (
    <>
    <Analytics />
    <Suspense fallback={<PageLoader />}>
    <Routes>
      {/* Public routes */}
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/invite" element={<InviteAccept />} />
      <Route path="/accept-invite" element={<InviteAccept />} />
      <Route path="/evals" element={<Evals />} />
      <Route path="/pricing" element={<Pricing />} />
      <Route path="/playground" element={<Playground />} />

      {/* Blog / Content pages */}
      <Route path="/blog" element={<Blog />} />
      <Route path="/blog/:slug" element={<BlogPost />} />

      {/* Resource / SEO content pages */}
      <Route path="/resources" element={<Resources />} />
      <Route path="/resources/:slug" element={<ResourcePage />} />

      {/* Integration workflow */}
      <Route path="/integration-workflow" element={<IntegrationWorkflow />} />

      {/* Google Ads landing pages */}
      <Route path="/solutions/ai-invoice-processing" element={<AdsLanding />} />
      <Route path="/solutions/automated-bank-reconciliation" element={<AdsLanding />} />
      <Route path="/solutions/payroll-automation" element={<AdsLanding />} />

      {/* Dashboard and all app routes — wrapped in Layout + ProtectedRoute */}
      <Route
        path="/dashboard"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo", "chro", "cmo", "coo", "auditor"]}>
            <Layout>
              <Dashboard />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/cfo"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo"]}>
            <Layout>
              <CFODashboard />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/cmo"
        element={
          <ProtectedRoute allowedRoles={["admin", "cmo"]}>
            <Layout>
              <CMODashboard />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/abm"
        element={
          <ProtectedRoute allowedRoles={["admin", "cmo"]}>
            <Layout>
              <ABMDashboard />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/report-schedules"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo", "cmo"]}>
            <Layout>
              <ReportScheduler />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/observatory"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo", "chro", "cmo", "coo"]}>
            <Layout>
              <Observatory />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/agents"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo", "chro", "cmo", "coo"]}>
            <Layout>
              <Agents />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/org-chart"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo", "chro", "cmo", "coo"]}>
            <Layout>
              <OrgChart />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/agents/new"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <Layout>
              <AgentCreate />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/agents/from-sop"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <Layout>
              <SOPUpload />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/agents/:id"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo", "chro", "cmo", "coo"]}>
            <Layout>
              <AgentDetail />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/workflows"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo", "chro", "cmo", "coo"]}>
            <Layout>
              <Workflows />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/workflows/new"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo", "chro", "cmo", "coo"]}>
            <Layout>
              <WorkflowCreate />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/workflows/:id"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo", "chro", "cmo", "coo"]}>
            <Layout>
              <WorkflowDetail />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/workflows/:id/runs/:runId"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo", "chro", "cmo", "coo"]}>
            <Layout>
              <WorkflowRun />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/approvals"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo", "chro", "cmo", "coo"]}>
            <Layout>
              <Approvals />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/integrations"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <Layout>
              <Integrations />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/connectors"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <Layout>
              <Connectors />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/prompt-templates"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <Layout>
              <PromptTemplates />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/connectors/new"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <Layout>
              <ConnectorCreate />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/connectors/:id"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <Layout>
              <ConnectorDetail />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/schemas"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <Layout>
              <Schemas />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/audit"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo", "chro", "cmo", "coo", "auditor"]}>
            <Layout>
              <Audit />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/onboarding"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <Onboarding />
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/sla"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <Layout>
              <SLAMonitor />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/sales"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <Layout>
              <SalesPipeline />
            </Layout>
          </ProtectedRoute>
        }
      />
      <Route
        path="/dashboard/settings"
        element={
          <ProtectedRoute allowedRoles={["admin"]}>
            <Layout>
              <Settings />
            </Layout>
          </ProtectedRoute>
        }
      />

      {/* 404 catch-all */}
      <Route path="*" element={<NotFound />} />
    </Routes>
    </Suspense>
    </>
  );
}

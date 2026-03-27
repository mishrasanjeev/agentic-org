import { lazy, Suspense } from "react";
import { Routes, Route } from "react-router-dom";
import Analytics from "./components/Analytics";
import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";

/* ── Critical path: Landing page loaded eagerly ── */
import Landing from "./pages/Landing";
import NotFound from "./pages/NotFound";

/* ── Everything else lazy-loaded ── */
const Login = lazy(() => import("./pages/Login"));
const Signup = lazy(() => import("./pages/Signup"));
const InviteAccept = lazy(() => import("./pages/InviteAccept"));
const Evals = lazy(() => import("./pages/Evals"));
const Pricing = lazy(() => import("./pages/Pricing"));
const Playground = lazy(() => import("./pages/Playground"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Agents = lazy(() => import("./pages/Agents"));
const AgentCreate = lazy(() => import("./pages/AgentCreate"));
const AgentDetail = lazy(() => import("./pages/AgentDetail"));
const Workflows = lazy(() => import("./pages/Workflows"));
const WorkflowCreate = lazy(() => import("./pages/WorkflowCreate"));
const WorkflowDetail = lazy(() => import("./pages/WorkflowDetail"));
const WorkflowRun = lazy(() => import("./pages/WorkflowRun"));
const Approvals = lazy(() => import("./pages/Approvals"));
const Connectors = lazy(() => import("./pages/Connectors"));
const ConnectorCreate = lazy(() => import("./pages/ConnectorCreate"));
const Schemas = lazy(() => import("./pages/Schemas"));
const Audit = lazy(() => import("./pages/Audit"));
const Observatory = lazy(() => import("./pages/Observatory"));
const Settings = lazy(() => import("./pages/Settings"));
const Onboarding = lazy(() => import("./pages/Onboarding"));
const SLAMonitor = lazy(() => import("./pages/SLAMonitor"));
const PromptTemplates = lazy(() => import("./pages/PromptTemplates"));
const SalesPipeline = lazy(() => import("./pages/SalesPipeline"));
const OrgChart = lazy(() => import("./pages/OrgChart"));

/* ── Blog / Content pages ── */
const Blog = lazy(() => import("./pages/blog/Blog"));
const BlogPost = lazy(() => import("./pages/blog/BlogPost"));

/* ── Google Ads landing pages ── */
const AdsLanding = lazy(() => import("./pages/ads/AdsLanding"));

/* ── Resource / SEO content pages ── */
const Resources = lazy(() => import("./pages/resources/Resources"));
const ResourcePage = lazy(() => import("./pages/resources/ResourcePage"));

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

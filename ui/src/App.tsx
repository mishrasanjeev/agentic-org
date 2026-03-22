import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import ProtectedRoute from "./components/ProtectedRoute";
import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Dashboard from "./pages/Dashboard";
import Agents from "./pages/Agents";
import AgentCreate from "./pages/AgentCreate";
import AgentDetail from "./pages/AgentDetail";
import Workflows from "./pages/Workflows";
import WorkflowCreate from "./pages/WorkflowCreate";
import WorkflowRun from "./pages/WorkflowRun";
import Approvals from "./pages/Approvals";
import Connectors from "./pages/Connectors";
import ConnectorCreate from "./pages/ConnectorCreate";
import Schemas from "./pages/Schemas";
import Audit from "./pages/Audit";
import Settings from "./pages/Settings";
import NotFound from "./pages/NotFound";

export default function App() {
  return (
    <Routes>
      {/* Public routes */}
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />

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
        path="/dashboard/agents/new"
        element={
          <ProtectedRoute allowedRoles={["admin", "cfo", "chro", "cmo", "coo"]}>
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
  );
}

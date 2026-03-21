import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
import Landing from "./pages/Landing";
import Dashboard from "./pages/Dashboard";
import Agents from "./pages/Agents";
import AgentDetail from "./pages/AgentDetail";
import Workflows from "./pages/Workflows";
import WorkflowRun from "./pages/WorkflowRun";
import Approvals from "./pages/Approvals";
import Connectors from "./pages/Connectors";
import Schemas from "./pages/Schemas";
import Audit from "./pages/Audit";
import Settings from "./pages/Settings";
import NotFound from "./pages/NotFound";

export default function App() {
  return (
    <Routes>
      {/* Public landing page — no Layout wrapper */}
      <Route path="/" element={<Landing />} />

      {/* Dashboard and all app routes — wrapped in Layout */}
      <Route
        path="/dashboard"
        element={
          <Layout>
            <Dashboard />
          </Layout>
        }
      />
      <Route
        path="/dashboard/agents"
        element={
          <Layout>
            <Agents />
          </Layout>
        }
      />
      <Route
        path="/dashboard/agents/:id"
        element={
          <Layout>
            <AgentDetail />
          </Layout>
        }
      />
      <Route
        path="/dashboard/workflows"
        element={
          <Layout>
            <Workflows />
          </Layout>
        }
      />
      <Route
        path="/dashboard/workflows/:id/runs/:runId"
        element={
          <Layout>
            <WorkflowRun />
          </Layout>
        }
      />
      <Route
        path="/dashboard/approvals"
        element={
          <Layout>
            <Approvals />
          </Layout>
        }
      />
      <Route
        path="/dashboard/connectors"
        element={
          <Layout>
            <Connectors />
          </Layout>
        }
      />
      <Route
        path="/dashboard/schemas"
        element={
          <Layout>
            <Schemas />
          </Layout>
        }
      />
      <Route
        path="/dashboard/audit"
        element={
          <Layout>
            <Audit />
          </Layout>
        }
      />
      <Route
        path="/dashboard/settings"
        element={
          <Layout>
            <Settings />
          </Layout>
        }
      />

      {/* 404 catch-all */}
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}

import { Routes, Route } from "react-router-dom";
import Layout from "./components/Layout";
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

export default function App() {
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/agents" element={<Agents />} />
        <Route path="/agents/:id" element={<AgentDetail />} />
        <Route path="/workflows" element={<Workflows />} />
        <Route path="/workflows/:id/runs/:runId" element={<WorkflowRun />} />
        <Route path="/approvals" element={<Approvals />} />
        <Route path="/connectors" element={<Connectors />} />
        <Route path="/schemas" element={<Schemas />} />
        <Route path="/audit" element={<Audit />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </Layout>
  );
}

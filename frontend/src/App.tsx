import { Routes, Route } from "react-router-dom";
import { AuthProvider } from "./auth/AuthContext";
import { ProtectedRoute } from "./auth/ProtectedRoute";
import Layout from "./components/Layout";
import Login from "./pages/Login";
import MissionControl from "./pages/MissionControl";
import Automations from "./pages/Automations";
import DeepDive from "./pages/DeepDive";
import Insights from "./pages/Insights";
import DataSources from "./pages/DataSources";
import AiModels from "./pages/AiModels";
import ProcessFlows from "./pages/ProcessFlows";
import AuditTrail from "./pages/AuditTrail";
import Users from "./pages/Users";

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route element={<ProtectedRoute><Layout /></ProtectedRoute>}>
          <Route path="/" element={<MissionControl />} />
          <Route path="/automations" element={<Automations />} />
          <Route path="/deep-dive" element={<DeepDive />} />
          <Route path="/insights" element={<Insights />} />
          <Route path="/data-sources" element={<ProtectedRoute role="admin"><DataSources /></ProtectedRoute>} />
          <Route path="/ai-models" element={<AiModels />} />
          <Route path="/process-flows" element={<ProcessFlows />} />
          <Route path="/audit-trail" element={<ProtectedRoute role="operator"><AuditTrail /></ProtectedRoute>} />
          <Route path="/users" element={<ProtectedRoute role="admin"><Users /></ProtectedRoute>} />
        </Route>
      </Routes>
    </AuthProvider>
  );
}

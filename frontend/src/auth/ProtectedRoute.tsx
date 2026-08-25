import { Navigate } from "react-router-dom";
import { useAuth, Role } from "./AuthContext";

export function ProtectedRoute({ children, role = "viewer" }: { children: React.ReactNode; role?: Role }) {
  const { user, loading, hasRole } = useAuth();
  if (loading) return <div className="empty">Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (!hasRole(role)) return <div className="empty">You don't have access to this page (requires role: {role}).</div>;
  return <>{children}</>;
}

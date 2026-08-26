import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

const NAV: { to: string; label: string; role: "viewer" | "operator" | "admin" }[] = [
  { to: "/", label: "Mission Control", role: "viewer" },
  { to: "/automations", label: "Automations", role: "viewer" },
  { to: "/deep-dive", label: "Deep Dive", role: "viewer" },
  { to: "/insights", label: "Insights", role: "viewer" },
  { to: "/data-sources", label: "Data Sources", role: "admin" },
  { to: "/ai-models", label: "AI Models", role: "viewer" },
  { to: "/process-flows", label: "Process Flows", role: "viewer" },
  { to: "/audit-trail", label: "Audit Trail", role: "operator" },
  { to: "/users", label: "Users", role: "admin" },
];

export default function Layout() {
  const { user, logout, hasRole } = useAuth();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          AI CONTROL TOWER
          <small>Connected Fleet · MCP Agent</small>
        </div>
        <nav>
          {NAV.filter((item) => hasRole(item.role)).map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
              {item.label}
            </NavLink>
          ))}
        </nav>
        <div className="role-badge">{user?.email} · {user?.role}</div>
        <button className="logout" onClick={() => logout()}>Sign out</button>
      </aside>
      <main className="content">
        <Outlet />
      </main>
    </div>
  );
}

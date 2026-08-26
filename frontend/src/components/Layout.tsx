import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";

// Small, hand-drawn icon set (no external library / no CDN) — one per nav
// route, sized and colored entirely through CSS (currentColor).
function IconRadar() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="8" />
      <circle cx="12" cy="12" r="3.2" />
      <circle cx="12" cy="12" r="1" fill="currentColor" stroke="none" />
    </svg>
  );
}
function IconBolt() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2 4 14h6l-1 8 9-12h-6l1-8Z" />
    </svg>
  );
}
function IconSearch() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="10.5" cy="10.5" r="6.5" />
      <line x1="20" y1="20" x2="15.5" y2="15.5" />
    </svg>
  );
}
function IconBars() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <line x1="5" y1="20" x2="5" y2="13" />
      <line x1="12" y1="20" x2="12" y2="8" />
      <line x1="19" y1="20" x2="19" y2="4" />
    </svg>
  );
}
function IconServer() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="4" width="18" height="6" rx="1.5" />
      <rect x="3" y="14" width="18" height="6" rx="1.5" />
      <circle cx="7" cy="7" r="0.6" fill="currentColor" stroke="none" />
      <circle cx="7" cy="17" r="0.6" fill="currentColor" stroke="none" />
    </svg>
  );
}
function IconChip() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="7" y="7" width="10" height="10" rx="1.5" />
      <line x1="12" y1="2" x2="12" y2="5" />
      <line x1="12" y1="19" x2="12" y2="22" />
      <line x1="2" y1="12" x2="5" y2="12" />
      <line x1="19" y1="12" x2="22" y2="12" />
    </svg>
  );
}
function IconBranch() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="6" cy="6" r="2.2" />
      <circle cx="6" cy="18" r="2.2" />
      <circle cx="18" cy="12" r="2.2" />
      <path d="M6 8.2V18" />
      <path d="M8.2 6H14a4 4 0 0 1 4 4v0" />
    </svg>
  );
}
function IconClock() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5V12l3 2" />
    </svg>
  );
}
function IconUsers() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="9" cy="8" r="3" />
      <path d="M3.5 19a5.5 5.5 0 0 1 11 0" />
      <circle cx="17.5" cy="9" r="2.4" />
      <path d="M15 19a4.6 4.6 0 0 1 5.8-4.4" />
    </svg>
  );
}

const NAV: { to: string; label: string; role: "viewer" | "operator" | "admin"; icon: () => JSX.Element }[] = [
  { to: "/", label: "Mission Control", role: "viewer", icon: IconRadar },
  { to: "/automations", label: "Automations", role: "viewer", icon: IconBolt },
  { to: "/deep-dive", label: "Deep Dive", role: "viewer", icon: IconSearch },
  { to: "/insights", label: "Insights", role: "viewer", icon: IconBars },
  { to: "/data-sources", label: "Data Sources", role: "admin", icon: IconServer },
  { to: "/ai-models", label: "AI Models", role: "viewer", icon: IconChip },
  { to: "/process-flows", label: "Process Flows", role: "viewer", icon: IconBranch },
  { to: "/audit-trail", label: "Audit Trail", role: "operator", icon: IconClock },
  { to: "/users", label: "Users", role: "admin", icon: IconUsers },
];

export default function Layout() {
  const { user, logout, hasRole } = useAuth();
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark">CT</span>
          <span className="brand-text">
            AI CONTROL TOWER
            <small>Connected Fleet · MCP Agent</small>
          </span>
        </div>
        <nav>
          {NAV.filter((item) => hasRole(item.role)).map((item) => (
            <NavLink key={item.to} to={item.to} end={item.to === "/"} className={({ isActive }) => (isActive ? "active" : "")}>
              <item.icon />
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

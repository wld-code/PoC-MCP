import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useLatestGuard } from "../api/useLatestGuard";
import { useAuth } from "../auth/AuthContext";

interface Flow { id: string; name: string; description: string; is_builtin: boolean; steps: any[] }

export default function ProcessFlows() {
  const { hasRole } = useAuth();
  const [flows, setFlows] = useState<Flow[]>([]);
  const [msg, setMsg] = useState("");

  const guard = useLatestGuard();
  async function refresh() { await guard(() => api.get("/api/flows"), (d) => setFlows(d.flows)); }
  useEffect(() => { refresh(); }, []);

  async function remove(id: string) { await api.del(`/api/flows/${id}`); await refresh(); }
  async function reset() {
    setMsg("");
    await api.post("/api/flows/reset");
    await refresh();
    setMsg("Reset to the built-in flows.");
  }

  return (
    <>
      <h1>Process Flows</h1>
      <p className="subtitle">The Deep Dive catalogue — ordered tool-call sequences, each with a why. Editing requires operator+.</p>

      {hasRole("operator") && (
        <button className="btn secondary" onClick={reset}>Reset to built-in flows</button>
      )}
      {msg && <div className="msg" style={{ marginTop: 10 }}>{msg}</div>}

      <div className="grid grid-2" style={{ marginTop: 16 }}>
        {flows.map((f) => (
          <div className="card" key={f.id}>
            <h3>{f.name} {f.is_builtin && <span className="pill">built-in</span>}</h3>
            <div style={{ fontSize: 13, color: "var(--slate)" }}>{f.description}</div>
            <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>{f.steps.length} steps</div>
            {hasRole("operator") && !f.is_builtin && (
              <button className="btn danger sm" style={{ marginTop: 10 }} onClick={() => remove(f.id)}>Delete</button>
            )}
          </div>
        ))}
      </div>
    </>
  );
}

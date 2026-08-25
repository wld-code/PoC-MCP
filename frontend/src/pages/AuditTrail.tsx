import { useEffect, useState } from "react";
import { api } from "../api/client";

interface AuditEntry { id: number; user_email: string | null; action: string; target_type: string; target_id: string; detail: Record<string, unknown>; created_at: string }
interface RunEntry { id: number; source: string; llm_label: string; question: string; answer: string; error: boolean; created_at: string }

export default function AuditTrail() {
  const [audit, setAudit] = useState<AuditEntry[]>([]);
  const [runs, setRuns] = useState<RunEntry[]>([]);

  useEffect(() => {
    api.get("/api/audit").then(setAudit).catch(() => {});
    api.get("/api/agents/runs").then(setRuns).catch(() => {});
  }, []);

  return (
    <>
      <h1>Audit Trail</h1>
      <p className="subtitle">Every user action and every agent run, attributed and timestamped.</p>

      <div className="card">
        <h3>Actions</h3>
        <table>
          <thead><tr><th>When</th><th>User</th><th>Action</th><th>Target</th></tr></thead>
          <tbody>
            {audit.map((a) => (
              <tr key={a.id}>
                <td>{new Date(a.created_at).toLocaleString()}</td>
                <td>{a.user_email || "—"}</td>
                <td><code>{a.action}</code></td>
                <td>{a.target_type} {a.target_id}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="card">
        <h3>Agent runs</h3>
        <table>
          <thead><tr><th>When</th><th>Source</th><th>LLM</th><th>Question</th><th>Status</th></tr></thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id}>
                <td>{new Date(r.created_at).toLocaleString()}</td>
                <td>{r.source}</td>
                <td>{r.llm_label}</td>
                <td style={{ maxWidth: 320 }}>{r.question}</td>
                <td><span className={`pill ${r.error ? "error" : "ok"}`}>{r.error ? "error" : "ok"}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

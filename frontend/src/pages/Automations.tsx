import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";

interface Schedule {
  id: number; label: string; question: string; llm_id: number | null; model: string;
  cron_expression: string | null; interval_seconds: number | null; active: boolean; created_at: string;
}
interface LlmOut { id: string; name: string }

export default function Automations() {
  const { hasRole } = useAuth();
  const [schedules, setSchedules] = useState<Schedule[]>([]);
  const [llms, setLlms] = useState<LlmOut[]>([]);
  const [label, setLabel] = useState("");
  const [question, setQuestion] = useState("");
  const [provider, setProvider] = useState("");
  const [mode, setMode] = useState<"interval" | "cron">("interval");
  const [interval, setIntervalVal] = useState(300);
  const [cron, setCron] = useState("0 * * * *");
  const [msg, setMsg] = useState("");

  useEffect(() => {
    api.get("/api/schedules").then(setSchedules).catch(() => {});
    api.get("/api/llms").then((d) => { setLlms(d.llms); setProvider(d.default || d.llms[0]?.id || ""); }).catch(() => {});
  }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setMsg("");
    try {
      await api.post("/api/schedules", {
        label: label || undefined, question, provider,
        cron_expression: mode === "cron" ? cron : null,
        interval_seconds: mode === "interval" ? interval : null,
      });
      setLabel(""); setQuestion("");
      setSchedules(await api.get("/api/schedules"));
      setMsg("Scheduled.");
    } catch (err: any) {
      setMsg(err.message);
    }
  }

  async function toggle(s: Schedule) {
    await api.post(`/api/schedules/${s.id}/${s.active ? "pause" : "resume"}`);
    setSchedules(await api.get("/api/schedules"));
  }
  async function remove(id: number) {
    await api.del(`/api/schedules/${id}`);
    setSchedules(await api.get("/api/schedules"));
  }

  return (
    <>
      <h1>Automations</h1>
      <p className="subtitle">Persistent, cron- or interval-based agent runs — survive a backend restart (APScheduler + Postgres).</p>

      {hasRole("operator") && (
        <div className="card">
          <h3>New automation</h3>
          <form onSubmit={create}>
            <label>Label (optional)</label>
            <input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Fleet anomaly scout" />
            <label>Question</label>
            <textarea rows={2} value={question} onChange={(e) => setQuestion(e.target.value)} required
                      placeholder="What is the main anomaly across connected services?" />
            <label>Agent (LLM)</label>
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              {llms.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
            </select>
            <label>Schedule</label>
            <div style={{ display: "flex", gap: 10 }}>
              <select value={mode} onChange={(e) => setMode(e.target.value as any)} style={{ width: 140 }}>
                <option value="interval">Every N seconds</option>
                <option value="cron">Cron expression</option>
              </select>
              {mode === "interval" ? (
                <input type="number" min={5} value={interval} onChange={(e) => setIntervalVal(Number(e.target.value))} />
              ) : (
                <input value={cron} onChange={(e) => setCron(e.target.value)} placeholder="0 * * * *" />
              )}
            </div>
            <button className="btn" style={{ marginTop: 12 }}>Schedule</button>
          </form>
          {msg && <div className="msg" style={{ marginTop: 10 }}>{msg}</div>}
        </div>
      )}

      <div className="card">
        <h3>Active automations</h3>
        {schedules.length === 0 ? <div className="empty">No automations yet.</div> : (
          <table>
            <thead><tr><th>Label</th><th>Schedule</th><th>Status</th><th>Created</th>{hasRole("operator") && <th /> }</tr></thead>
            <tbody>
              {schedules.map((s) => (
                <tr key={s.id}>
                  <td>{s.label}<div style={{ color: "var(--muted)", fontSize: 12 }}>{s.question}</div></td>
                  <td>{s.cron_expression || `every ${s.interval_seconds}s`}</td>
                  <td><span className={`pill ${s.active ? "ok" : ""}`}>{s.active ? "active" : "paused"}</span></td>
                  <td>{new Date(s.created_at).toLocaleString()}</td>
                  {hasRole("operator") && (
                    <td>
                      <button className="btn secondary sm" onClick={() => toggle(s)}>{s.active ? "Pause" : "Resume"}</button>{" "}
                      <button className="btn danger sm" onClick={() => remove(s.id)}>Delete</button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

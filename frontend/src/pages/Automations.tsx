import { Fragment, useEffect, useState } from "react";
import { api } from "../api/client";
import { useLatestGuard } from "../api/useLatestGuard";
import { useAuth } from "../auth/AuthContext";
import { EvidencePanel } from "../components/EvidencePanel";

interface Schedule {
  id: number; label: string; question: string; llm_id: number | null; model: string;
  cron_expression: string | null; interval_seconds: number | null; active: boolean; created_at: string;
  next_run_time: string | null;
}
interface RunOut {
  id: number; source: string; llm_label: string; model: string; question: string;
  answer: string; tool_calls: { name: string; arguments: Record<string, unknown> }[]; error: boolean; created_at: string;
}
interface LlmOut { id: string; name: string }

function formatCountdown(totalSeconds: number): string {
  const s = Math.max(0, totalSeconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) return `${h}h ${m}m ${sec}s`;
  if (m > 0) return `${m}m ${sec}s`;
  return `${sec}s`;
}

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
  const [now, setNow] = useState(() => Date.now());
  const [expanded, setExpanded] = useState<number | null>(null);
  const [runsById, setRunsById] = useState<Record<number, RunOut[]>>({});
  const [runsBusy, setRunsBusy] = useState<number | null>(null);

  const guard = useLatestGuard();
  async function refreshSchedules() { await guard(() => api.get("/api/schedules"), setSchedules); }

  async function loadRuns(id: number) {
    setRunsBusy(id);
    try {
      const runs = await api.get(`/api/schedules/${id}/runs`);
      setRunsById((prev) => ({ ...prev, [id]: runs }));
    } finally {
      setRunsBusy((b) => (b === id ? null : b));
    }
  }
  function toggleRuns(id: number) {
    if (expanded === id) { setExpanded(null); return; }
    setExpanded(id);
    loadRuns(id);
  }

  useEffect(() => {
    refreshSchedules();
    api.get("/api/llms").then((d) => { setLlms(d.llms); setProvider(d.default || d.llms[0]?.id || ""); }).catch(() => {});
  }, []);

  // Tick every second so "next run in ..." actually counts down, and poll the
  // schedule list + the open runs panel so a fired job's countdown resets and
  // its new result shows up without a manual refresh.
  useEffect(() => {
    const tick = setInterval(() => setNow(Date.now()), 1000);
    const poll = setInterval(() => {
      refreshSchedules();
      setExpanded((id) => { if (id !== null) loadRuns(id); return id; });
    }, 5000);
    return () => { clearInterval(tick); clearInterval(poll); };
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
      await refreshSchedules();
      setMsg("Scheduled.");
    } catch (err: any) {
      setMsg(err.message);
    }
  }

  async function toggle(s: Schedule) {
    await api.post(`/api/schedules/${s.id}/${s.active ? "pause" : "resume"}`);
    await refreshSchedules();
  }
  async function remove(id: number) {
    await api.del(`/api/schedules/${id}`);
    await refreshSchedules();
  }

  function nextRunLabel(s: Schedule): string {
    if (!s.active) return "paused";
    if (!s.next_run_time) return "—";
    const secs = Math.round((new Date(s.next_run_time).getTime() - now) / 1000);
    return secs <= 0 ? "due now" : `in ${formatCountdown(secs)}`;
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
            <thead>
              <tr>
                <th>Label</th><th>Schedule</th><th>Next run</th><th>Status</th><th>Created</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {schedules.map((s) => (
                <Fragment key={s.id}>
                  <tr>
                    <td>{s.label}<div style={{ color: "var(--muted)", fontSize: 12 }}>{s.question}</div></td>
                    <td>{s.cron_expression || `every ${s.interval_seconds}s`}</td>
                    <td>{nextRunLabel(s)}</td>
                    <td><span className={`pill ${s.active ? "ok" : ""}`}>{s.active ? "active" : "paused"}</span></td>
                    <td>{new Date(s.created_at).toLocaleString()}</td>
                    <td>
                      <button className="btn secondary sm" onClick={() => toggleRuns(s.id)}>
                        {expanded === s.id ? "Hide results" : "Results"}
                      </button>{" "}
                      {hasRole("operator") && (
                        <>
                          <button className="btn secondary sm" onClick={() => toggle(s)}>{s.active ? "Pause" : "Resume"}</button>{" "}
                          <button className="btn danger sm" onClick={() => remove(s.id)}>Delete</button>
                        </>
                      )}
                    </td>
                  </tr>
                  {expanded === s.id && (
                    <tr>
                      <td colSpan={6} style={{ background: "var(--offwhite)" }}>
                        {runsBusy === s.id && !runsById[s.id] ? (
                          <div className="empty">Loading results…</div>
                        ) : (runsById[s.id]?.length ?? 0) === 0 ? (
                          <div className="empty">No runs yet — this fires every {s.cron_expression || `${s.interval_seconds}s`}.</div>
                        ) : (
                          <div style={{ padding: "8px 4px" }}>
                            {runsById[s.id].map((r) => (
                              <div key={r.id} style={{ paddingBottom: 14, marginBottom: 14, borderBottom: "1px solid var(--ice)" }}>
                                <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 6 }}>
                                  <span className={`pill ${r.error ? "error" : "ok"}`}>{r.error ? "error" : "ok"}</span>
                                  <span style={{ fontSize: 12, color: "var(--muted)" }}>
                                    {new Date(r.created_at).toLocaleString()} · {r.llm_label}
                                  </span>
                                </div>
                                <div className="chat-answer">{r.answer}</div>
                                <EvidencePanel toolCalls={r.tool_calls} />
                              </div>
                            ))}
                          </div>
                        )}
                      </td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}

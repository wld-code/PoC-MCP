import { useState } from "react";
import { api } from "../api/client";
import { EvidencePanel } from "../components/EvidencePanel";

async function tool(name: string, args: Record<string, unknown> = {}) {
  const res = await api.post("/api/tool", { name, arguments: args });
  return res.result as string;
}

export default function Insights() {
  const [usage, setUsage] = useState<string>("");
  const [apps, setApps] = useState<string>("");
  const [anomalies, setAnomalies] = useState<string>("");
  const [loading, setLoading] = useState(false);
  const [brief, setBrief] = useState<{ answer: string; tool_calls: any[] } | null>(null);
  const [briefBusy, setBriefBusy] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const [u, a, an] = await Promise.all([
        tool("service_usage", { period: "30d" }),
        tool("top_applications", { limit: 5 }),
        tool("anomalies"),
      ]);
      setUsage(u); setApps(a); setAnomalies(an);
    } finally {
      setLoading(false);
    }
  }

  async function generateBrief() {
    setBriefBusy(true);
    try {
      const res = await api.post("/api/agents/run", {
        question: "Give me an executive brief of fleet-wide usage, the fastest-growing applications, and any flagged anomalies — with clear numbers.",
      });
      setBrief(res);
    } catch (err: any) {
      setBrief({ answer: `⚠️ ${err.message}`, tool_calls: [] });
    } finally {
      setBriefBusy(false);
    }
  }

  return (
    <>
      <h1>Insights</h1>
      <p className="subtitle">Business insights auto-derived from the data lake.</p>

      <button className="btn secondary" onClick={load} disabled={loading}>{loading ? "Loading…" : "Load fleet insights"}</button>

      {(usage || apps || anomalies) && (
        <div className="grid grid-3" style={{ marginTop: 16 }}>
          <div className="card"><h3>Usage (30d)</h3><pre style={{ fontSize: 11, whiteSpace: "pre-wrap" }}>{usage}</pre></div>
          <div className="card"><h3>Top applications</h3><pre style={{ fontSize: 11, whiteSpace: "pre-wrap" }}>{apps}</pre></div>
          <div className="card"><h3>Anomalies</h3><pre style={{ fontSize: 11, whiteSpace: "pre-wrap" }}>{anomalies}</pre></div>
        </div>
      )}

      <div className="card" style={{ marginTop: 16 }}>
        <h3>Executive brief</h3>
        <button className="btn" onClick={generateBrief} disabled={briefBusy}>{briefBusy ? "Generating…" : "Generate LLM executive brief"}</button>
        {brief && (
          <div style={{ marginTop: 12 }}>
            <div className="chat-answer">{brief.answer}</div>
            <EvidencePanel toolCalls={brief.tool_calls} />
          </div>
        )}
      </div>
    </>
  );
}

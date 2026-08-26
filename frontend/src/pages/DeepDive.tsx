import { useEffect, useState } from "react";
import { api } from "../api/client";
import { EvidencePanel } from "../components/EvidencePanel";

interface FlowStep { system: string; tool: string; what: string; why: string; args: Record<string, string>; capture: string[]; skip_note?: string | null }
interface Flow { id: string; name: string; description: string; inputs: { key: string; label: string; placeholder: string; default: string; required: boolean }[]; steps: FlowStep[] }

function fillTemplate(tpl: string, values: Record<string, string>): string {
  return tpl.replace(/\{(\w+)\}/g, (_, k) => values[k] ?? "");
}

// The fake APIs return back-to-back pretty-printed JSON objects (no
// enclosing array, no commas) rather than one JSON document — split on
// balanced braces and parse each individually.
function parseConcatenatedJson(text: string): any[] {
  const out: any[] = [];
  let depth = 0, start = -1;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (ch === "{") { if (depth === 0) start = i; depth++; }
    else if (ch === "}") {
      depth--;
      if (depth === 0 && start >= 0) {
        try { out.push(JSON.parse(text.slice(start, i + 1))); } catch { /* skip malformed chunk */ }
        start = -1;
      }
    }
  }
  return out;
}

// list_vehicles takes no filter — it always returns the whole fleet — so
// resolving "vin" by grabbing the first "vin" key in the result would always
// pick the same (first) vehicle regardless of what the user typed. Match the
// typed input(s) against vin/owner instead, falling back to the first entry.
function resolveVin(resultText: string, queries: string[]): string | undefined {
  const vehicles = parseConcatenatedJson(resultText).filter((o) => typeof o?.vin === "string");
  if (!vehicles.length) return undefined;
  const cleaned = queries.map((q) => (q || "").trim().toLowerCase()).filter(Boolean);
  for (const q of cleaned) {
    const exact = vehicles.find((v) => v.vin.toLowerCase() === q);
    if (exact) return exact.vin;
  }
  for (const q of cleaned) {
    const byOwner = vehicles.find((v) => typeof v.owner === "string" && v.owner.toLowerCase().includes(q));
    if (byOwner) return byOwner.vin;
  }
  return vehicles[0].vin;
}

export default function DeepDive() {
  const [flows, setFlows] = useState<Flow[]>([]);
  const [selected, setSelected] = useState<Flow | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<{ step: FlowStep; result?: string; ok?: boolean; skipped?: boolean }[]>([]);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiExplanation, setAiExplanation] = useState<{ answer: string; tool_calls: any[] } | null>(null);

  useEffect(() => { api.get("/api/flows").then((d) => setFlows(d.flows)).catch(() => {}); }, []);

  function pick(flow: Flow) {
    setSelected(flow);
    const initial: Record<string, string> = {};
    flow.inputs.forEach((i) => (initial[i.key] = i.default || ""));
    setInputs(initial);
    setResults([]);
    setAiExplanation(null);
  }

  async function executeFlow(flow: Flow, values: Record<string, string>) {
    const captured: Record<string, string> = { ...values };
    const out: typeof results = [];
    for (const step of flow.steps) {
      const missingCapture = step.args && Object.values(step.args).some((v) => /\{(\w+)\}/.test(v) && !captured[v.replace(/[{}]/g, "")]);
      if (missingCapture) {
        out.push({ step, skipped: true });
        setResults([...out]);
        continue;
      }
      const args: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(step.args)) args[k] = fillTemplate(v, captured);
      try {
        const res = await api.post("/api/tool", { name: step.tool, arguments: args });
        out.push({ step, result: res.result, ok: res.ok });
        for (const cap of step.capture) {
          let value: string | undefined;
          if (step.tool === "list_vehicles" && cap === "vin") {
            value = resolveVin(res.result, Object.values(values));
          } else {
            // Match "vin", "campaign_id", or "last_campaign_id" alike — API
            // responses don't always use the bare key the flow asks to
            // capture (e.g. drift is reported under last_campaign_id).
            value = new RegExp(`"(?:last_)?${cap}(?:_id)?"\\s*:\\s*"([^"]+)"`).exec(res.result)?.[1];
          }
          if (value) captured[cap] = value;
        }
      } catch (err: any) {
        out.push({ step, result: err.message, ok: false });
      }
      setResults([...out]);
    }
    return out;
  }

  async function run() {
    if (!selected) return;
    setRunning(true);
    setAiExplanation(null);
    await executeFlow(selected, inputs);
    setRunning(false);
  }

  async function runWithAI() {
    if (!selected) return;
    setRunning(true);
    setAiExplanation(null);
    const flow = selected;
    const values = inputs;
    const out = await executeFlow(flow, values);
    setRunning(false);

    setAiBusy(true);
    try {
      const summary = out
        .map((r, i) => {
          if (r.skipped) return `${i + 1}. [${r.step.system}] ${r.step.tool} — skipped (${r.step.skip_note || "a required input wasn't available"})`;
          return `${i + 1}. [${r.step.system}] ${r.step.tool} — ${r.ok ? "ok" : "error"}: ${(r.result || "").slice(0, 500)}`;
        })
        .join("\n");
      const question =
        `I just ran the "${flow.name}" process flow (inputs: ${JSON.stringify(values)}). ` +
        `Here are the real step-by-step results, in order:\n\n${summary}\n\n` +
        `Explain in plain language what happened, whether the flow succeeded end-to-end, and call out anything ` +
        `unusual (errors, skipped steps, drift between desired and actual state).`;
      const res = await api.post("/api/agents/run", { question });
      setAiExplanation({ answer: res.answer, tool_calls: res.tool_calls });
    } catch (err: any) {
      setAiExplanation({ answer: `⚠️ ${err.message}`, tool_calls: [] });
    } finally {
      setAiBusy(false);
    }
  }

  return (
    <>
      <h1>Deep Dive</h1>
      <p className="subtitle">Run a process flow step by step for a vehicle, and see each tool's real result.</p>

      <div className="grid grid-3">
        {flows.map((f) => (
          <div key={f.id} className={`card clickable ${selected?.id === f.id ? "selected" : ""}`} onClick={() => pick(f)}>
            <h3>{f.name}</h3>
            <div style={{ fontSize: 12, color: "var(--slate)" }}>{f.description}</div>
          </div>
        ))}
      </div>

      {selected && (
        <div className="card">
          <h3>{selected.name}</h3>
          {selected.inputs.map((inp) => (
            <div key={inp.key}>
              <label>{inp.label}</label>
              <input value={inputs[inp.key] || ""} placeholder={inp.placeholder}
                     onChange={(e) => setInputs({ ...inputs, [inp.key]: e.target.value })} />
            </div>
          ))}
          <div style={{ marginTop: 12, display: "flex", gap: 8 }}>
            <button className="btn" onClick={run} disabled={running || aiBusy}>{running ? "Running…" : "Run flow"}</button>
            <button className="btn secondary" onClick={runWithAI} disabled={running || aiBusy}>
              {running ? "Running…" : aiBusy ? "Explaining…" : "Run flow with AI"}
            </button>
          </div>

          {(results.length > 0 || aiBusy || aiExplanation) && (
            <div className="grid grid-2" style={{ marginTop: 16, alignItems: "start" }}>
              {results.length > 0 && (
                <table>
                  <thead><tr><th>System</th><th>Tool</th><th>Why</th><th>Result</th></tr></thead>
                  <tbody>
                    {results.map((r, i) => (
                      <tr key={i}>
                        <td>{r.step.system}</td>
                        <td><code>{r.step.tool}</code></td>
                        <td style={{ maxWidth: 260 }}>{r.step.why}</td>
                        <td style={{ maxWidth: 320, wordBreak: "break-word" }}>
                          {r.skipped ? <span className="pill">{r.step.skip_note || "skipped"}</span> :
                            <span className={`pill ${r.ok ? "ok" : "error"}`}>{r.ok ? "ok" : "error"}</span>}
                          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>{r.result?.slice(0, 200)}</div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {(aiBusy || aiExplanation) && (
                <div className="card">
                  <h3>AI explanation</h3>
                  {aiBusy && !aiExplanation && <div className="subtitle">Explaining…</div>}
                  {aiExplanation && (
                    <>
                      <div className="chat-answer">{aiExplanation.answer}</div>
                      <EvidencePanel toolCalls={aiExplanation.tool_calls} />
                    </>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </>
  );
}

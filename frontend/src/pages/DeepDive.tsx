import { useEffect, useState } from "react";
import { api } from "../api/client";

interface FlowStep { system: string; tool: string; what: string; why: string; args: Record<string, string>; capture: string[]; skip_note?: string | null }
interface Flow { id: string; name: string; description: string; inputs: { key: string; label: string; placeholder: string; default: string; required: boolean }[]; steps: FlowStep[] }

function fillTemplate(tpl: string, values: Record<string, string>): string {
  return tpl.replace(/\{(\w+)\}/g, (_, k) => values[k] ?? "");
}

export default function DeepDive() {
  const [flows, setFlows] = useState<Flow[]>([]);
  const [selected, setSelected] = useState<Flow | null>(null);
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [running, setRunning] = useState(false);
  const [results, setResults] = useState<{ step: FlowStep; result?: string; ok?: boolean; skipped?: boolean }[]>([]);

  useEffect(() => { api.get("/api/flows").then((d) => setFlows(d.flows)).catch(() => {}); }, []);

  function pick(flow: Flow) {
    setSelected(flow);
    const initial: Record<string, string> = {};
    flow.inputs.forEach((i) => (initial[i.key] = i.default || ""));
    setInputs(initial);
    setResults([]);
  }

  async function run() {
    if (!selected) return;
    setRunning(true);
    const captured: Record<string, string> = { ...inputs };
    const out: typeof results = [];
    for (const step of selected.steps) {
      const missingCapture = step.args && Object.values(step.args).some((v) => /\{(\w+)\}/.test(v) && !captured[v.replace(/[{}]/g, "")]);
      if (missingCapture) {
        out.push({ step, skipped: true });
        continue;
      }
      const args: Record<string, unknown> = {};
      for (const [k, v] of Object.entries(step.args)) args[k] = fillTemplate(v, captured);
      try {
        const res = await api.post("/api/tool", { name: step.tool, arguments: args });
        out.push({ step, result: res.result, ok: res.ok });
        for (const cap of step.capture) {
          const m = new RegExp(`"${cap}"\\s*:\\s*"([^"]+)"`).exec(res.result) || new RegExp(`"${cap}_id"\\s*:\\s*"([^"]+)"`).exec(res.result);
          if (m) captured[cap] = m[1];
        }
      } catch (err: any) {
        out.push({ step, result: err.message, ok: false });
      }
      setResults([...out]);
    }
    setRunning(false);
  }

  return (
    <>
      <h1>Deep Dive</h1>
      <p className="subtitle">Run a process flow step by step for a vehicle, and see each tool's real result.</p>

      <div className="grid grid-3">
        {flows.map((f) => (
          <div key={f.id} className={`card`} style={{ cursor: "pointer", borderColor: selected?.id === f.id ? "var(--blue)" : undefined }} onClick={() => pick(f)}>
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
          <button className="btn" style={{ marginTop: 12 }} onClick={run} disabled={running}>{running ? "Running…" : "Run flow"}</button>

          {results.length > 0 && (
            <table style={{ marginTop: 16 }}>
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
        </div>
      )}
    </>
  );
}

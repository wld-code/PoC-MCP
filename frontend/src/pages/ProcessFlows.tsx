import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { useLatestGuard } from "../api/useLatestGuard";
import { useAuth } from "../auth/AuthContext";

interface FlowInput { key: string; label: string; placeholder: string; default: string; required: boolean }
interface FlowStep { system: string; tool: string; what: string; why: string; args: Record<string, string>; capture: string[]; skip_note?: string | null }
interface Flow { id: string; name: string; description: string; is_builtin: boolean; inputs: FlowInput[]; steps: FlowStep[] }
interface Draft { name: string; description: string; inputs: FlowInput[]; steps: FlowStep[] }
interface ToolInfo { name: string; description: string; system: string }

// Known systems get a stable brand color; anything custom still gets a
// stable (if arbitrary) color derived from its name, so the diagram never
// looks unfinished no matter what an admin types.
const SYSTEM_COLORS: Record<string, string> = {
  cvc: "#1456d6", asap: "#7c3aed", redbend: "#d97706", "data lake": "#0f9d78", datalake: "#0f9d78",
};
function systemColor(name: string): string {
  const key = (name || "").trim().toLowerCase();
  if (!key) return "#8a98ad";
  if (SYSTEM_COLORS[key]) return SYSTEM_COLORS[key];
  for (const [k, v] of Object.entries(SYSTEM_COLORS)) if (key.includes(k)) return v;
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return `hsl(${hash % 360}, 55%, 42%)`;
}

// The MCP server names the backend reports (see Data Sources) translated to
// the short display labels used throughout the app's flows.
const SERVER_LABELS: Record<string, string> = {
  "asap-orchestrator": "ASAP", "cvc-gateway": "CVC", "redbend-ota": "Redbend", "datalake-insights": "Data Lake",
};

const BLANK_STEP: FlowStep = { system: "", tool: "", what: "", why: "", args: {}, capture: [], skip_note: "" };

function blankDraft(): Draft {
  return { name: "", description: "", inputs: [], steps: [{ ...BLANK_STEP }] };
}

function toDraft(f: Flow): Draft {
  return {
    name: f.name,
    description: f.description,
    inputs: f.inputs.map((i) => ({ ...i })),
    steps: f.steps.length ? f.steps.map((s) => ({ ...s, args: { ...s.args }, capture: [...s.capture] })) : [{ ...BLANK_STEP }],
  };
}

function placeholders(tpl: string): string[] {
  return [...tpl.matchAll(/\{(\w+)\}/g)].map((m) => m[1]);
}

export default function ProcessFlows() {
  const { hasRole } = useAuth();
  const canEdit = hasRole("operator"); // operator+ — admin is always >= operator, so admins always have this
  const [flows, setFlows] = useState<Flow[]>([]);
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [msg, setMsg] = useState<{ text: string; error?: boolean } | null>(null);
  const [editingId, setEditingId] = useState<string | "new" | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [saving, setSaving] = useState(false);

  const guard = useLatestGuard();
  async function refresh() { await guard(() => api.get("/api/flows"), (d) => setFlows(d.flows)); }
  useEffect(() => {
    refresh();
    api.get("/api/info").then((info) => {
      const list: ToolInfo[] = [];
      for (const s of info.servers || []) {
        const label = SERVER_LABELS[s.name] || s.name;
        for (const t of s.tools || []) list.push({ name: t.name, description: t.description, system: label });
      }
      setTools(list);
    }).catch(() => {});
  }, []);

  const toolByName = useMemo(() => Object.fromEntries(tools.map((t) => [t.name, t])), [tools]);
  const knownSystems = useMemo(() => {
    const s = new Set<string>(Object.values(SERVER_LABELS));
    flows.forEach((f) => f.steps.forEach((st) => st.system && s.add(st.system)));
    return [...s];
  }, [flows]);

  function startNew() {
    setDraft(blankDraft());
    setEditingId("new");
    setMsg(null);
  }
  function startEdit(f: Flow) {
    setDraft(toDraft(f));
    setEditingId(f.id);
    setMsg(null);
  }
  function cancelEdit() {
    setDraft(null);
    setEditingId(null);
  }

  function updateDraft(patch: Partial<Draft>) { setDraft((d) => (d ? { ...d, ...patch } : d)); }

  function addInput() {
    updateDraft({ inputs: [...(draft?.inputs || []), { key: "", label: "", placeholder: "", default: "", required: true }] });
  }
  function updateInput(i: number, patch: Partial<FlowInput>) {
    if (!draft) return;
    const inputs = draft.inputs.map((inp, idx) => (idx === i ? { ...inp, ...patch } : inp));
    updateDraft({ inputs });
  }
  function removeInput(i: number) {
    if (!draft) return;
    updateDraft({ inputs: draft.inputs.filter((_, idx) => idx !== i) });
  }

  function addStep() { updateDraft({ steps: [...(draft?.steps || []), { ...BLANK_STEP, args: {}, capture: [] }] }); }
  function removeStep(i: number) {
    if (!draft) return;
    updateDraft({ steps: draft.steps.filter((_, idx) => idx !== i) });
  }
  function moveStep(i: number, dir: -1 | 1) {
    if (!draft) return;
    const j = i + dir;
    if (j < 0 || j >= draft.steps.length) return;
    const steps = [...draft.steps];
    [steps[i], steps[j]] = [steps[j], steps[i]];
    updateDraft({ steps });
  }
  function updateStep(i: number, patch: Partial<FlowStep>) {
    if (!draft) return;
    const steps = draft.steps.map((s, idx) => (idx === i ? { ...s, ...patch } : s));
    updateDraft({ steps });
  }
  function pickTool(i: number, toolName: string) {
    const info = toolByName[toolName];
    const step = draft?.steps[i];
    updateStep(i, { tool: toolName, system: step?.system || info?.system || "" });
  }
  function setArgs(i: number, args: Record<string, string>) { updateStep(i, { args }); }
  function addArg(i: number) {
    const step = draft?.steps[i]; if (!step) return;
    setArgs(i, { ...step.args, "": "" });
  }
  function updateArg(i: number, oldKey: string, newKey: string, value: string) {
    const step = draft?.steps[i]; if (!step) return;
    const args: Record<string, string> = {};
    for (const [k, v] of Object.entries(step.args)) args[k === oldKey ? newKey : k] = k === oldKey ? value : v;
    setArgs(i, args);
  }
  function removeArg(i: number, key: string) {
    const step = draft?.steps[i]; if (!step) return;
    const args = { ...step.args }; delete args[key];
    setArgs(i, args);
  }

  const unresolved = useMemo(() => {
    if (!draft) return [];
    const known = new Set(draft.inputs.map((i) => i.key));
    const missing = new Set<string>();
    for (const step of draft.steps) {
      for (const v of Object.values(step.args)) for (const p of placeholders(v)) if (!known.has(p)) missing.add(p);
      for (const c of step.capture) known.add(c);
    }
    return [...missing];
  }, [draft]);

  async function save() {
    if (!draft) return;
    if (!draft.name.trim()) { setMsg({ text: "Name is required.", error: true }); return; }
    if (draft.steps.some((s) => !s.tool.trim())) { setMsg({ text: "Every step needs a tool.", error: true }); return; }
    setSaving(true);
    setMsg(null);
    try {
      const body = {
        name: draft.name.trim(),
        description: draft.description,
        inputs: draft.inputs.filter((i) => i.key.trim()),
        steps: draft.steps.map((s) => ({
          system: s.system, tool: s.tool.trim(), what: s.what, why: s.why,
          args: Object.fromEntries(Object.entries(s.args).filter(([k]) => k.trim())),
          capture: s.capture.map((c) => c.trim()).filter(Boolean),
          skip_note: s.skip_note || null,
        })),
      };
      if (editingId === "new") {
        await api.post("/api/flows", body);
        setMsg({ text: `"${body.name}" created.` });
      } else if (editingId) {
        await api.put(`/api/flows/${editingId}`, body);
        setMsg({ text: `"${body.name}" updated.` });
      }
      cancelEdit();
      await refresh();
    } catch (err: any) {
      setMsg({ text: err.message, error: true });
    } finally {
      setSaving(false);
    }
  }

  async function remove(id: string) {
    await api.del(`/api/flows/${id}`);
    if (editingId === id) cancelEdit();
    await refresh();
  }
  async function reset() {
    setMsg(null);
    await api.post("/api/flows/reset");
    cancelEdit();
    await refresh();
    setMsg({ text: "Reset to the built-in flows." });
  }

  return (
    <>
      <h1>Process Flows</h1>
      <p className="subtitle">The Deep Dive catalogue — ordered tool-call sequences, each with a why. Editing requires operator+ (admins always qualify).</p>

      {canEdit && (
        <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
          <button className="btn" onClick={startNew} disabled={editingId !== null}>+ New process flow</button>
          <button className="btn secondary" onClick={reset}>Reset to built-in flows</button>
        </div>
      )}
      {msg && <div className={`msg ${msg.error ? "error" : "ok"}`}>{msg.text}</div>}

      {draft && (
        <div className="card flow-editor">
          <h3>{editingId === "new" ? "New process flow" : `Editing "${flows.find((f) => f.id === editingId)?.name}"`}</h3>

          <label>Name</label>
          <input value={draft.name} onChange={(e) => updateDraft({ name: e.target.value })} placeholder="e.g. Roadside Diagnostics" />
          <label>Description</label>
          <textarea rows={2} value={draft.description} onChange={(e) => updateDraft({ description: e.target.value })}
                     placeholder="What this flow demonstrates, in one or two sentences." />

          {/* Flow-at-a-glance: a compact colored sequence so the shape of the
              flow is visible before reading any step detail. */}
          <div className="flow-glance">
            {draft.steps.map((s, i) => (
              <span className="seg" key={i}>
                {i > 0 && <span className="arrow">→</span>}
                <span className="dot" style={{ background: systemColor(s.system) }} />
                <span>{s.tool || <em style={{ color: "var(--muted)" }}>(tool)</em>}</span>
              </span>
            ))}
          </div>

          <label style={{ marginTop: 18 }}>Inputs the user fills in (e.g. a VIN)</label>
          {draft.inputs.map((inp, i) => (
            <div className="input-row" key={i}>
              <input value={inp.key} onChange={(e) => updateInput(i, { key: e.target.value })} placeholder="key (e.g. vehicle)" />
              <input value={inp.label} onChange={(e) => updateInput(i, { label: e.target.value })} placeholder="Label" />
              <input value={inp.placeholder} onChange={(e) => updateInput(i, { placeholder: e.target.value })} placeholder="Placeholder" />
              <input value={inp.default} onChange={(e) => updateInput(i, { default: e.target.value })} placeholder="Default" />
              <label className="checkbox-inline">
                <input type="checkbox" checked={inp.required} onChange={(e) => updateInput(i, { required: e.target.checked })} /> required
              </label>
              <button className="btn danger sm" onClick={() => removeInput(i)}>✕</button>
            </div>
          ))}
          <button className="btn secondary sm" style={{ marginTop: 8 }} onClick={addInput}>+ Add input</button>

          <label style={{ marginTop: 20 }}>Steps</label>
          <div className="flow-timeline">
            {draft.steps.map((step, i) => (
              <div className="flow-step" key={i}>
                <div className="badge" style={{ background: systemColor(step.system) }}>{i + 1}</div>
                <div className="step-card" style={{ borderLeftColor: systemColor(step.system) }}>
                  <div className="step-card-head">
                    <div className="flow-step-row">
                      <div>
                        <label>System</label>
                        <input value={step.system} onChange={(e) => updateStep(i, { system: e.target.value })}
                               list="system-options" placeholder="e.g. CVC" />
                      </div>
                      <div>
                        <label>Tool</label>
                        <input value={step.tool} onChange={(e) => pickTool(i, e.target.value)}
                               list="tool-options" placeholder="e.g. get_vehicle" />
                        {toolByName[step.tool] && (
                          <div className="hint">{toolByName[step.tool].description.split("\n")[0].trim()}</div>
                        )}
                      </div>
                    </div>
                    <div className="step-actions">
                      <button className="btn secondary sm" onClick={() => moveStep(i, -1)} disabled={i === 0} title="Move up">↑</button>
                      <button className="btn secondary sm" onClick={() => moveStep(i, 1)} disabled={i === draft.steps.length - 1} title="Move down">↓</button>
                      <button className="btn danger sm" onClick={() => removeStep(i)} disabled={draft.steps.length <= 1} title="Remove step">✕</button>
                    </div>
                  </div>

                  <label>What (short label)</label>
                  <input value={step.what} onChange={(e) => updateStep(i, { what: e.target.value })} placeholder="Resolve the vehicle and confirm it exists" />
                  <label>Why (shown in the results table)</label>
                  <textarea rows={2} value={step.why} onChange={(e) => updateStep(i, { why: e.target.value })}
                            placeholder="Explain, for a reader, why this step matters." />

                  <label>Arguments {"(use {key} to substitute an input or a captured value)"}</label>
                  {Object.entries(step.args).map(([k, v], ai) => (
                    <div className="arg-row" key={ai}>
                      <input value={k} onChange={(e) => updateArg(i, k, e.target.value, v)} placeholder="arg name (e.g. vin)" />
                      <input value={v} onChange={(e) => updateArg(i, k, k, e.target.value)} placeholder="value (e.g. {vin})" />
                      <button className="btn danger sm" onClick={() => removeArg(i, k)}>✕</button>
                    </div>
                  ))}
                  <button className="btn secondary sm" style={{ marginTop: 6 }} onClick={() => addArg(i)}>+ Add argument</button>

                  <div className="flow-step-row" style={{ marginTop: 10 }}>
                    <div>
                      <label>Capture (comma-separated names pulled out of the result, e.g. vin, campaign)</label>
                      <input value={step.capture.join(", ")}
                             onChange={(e) => updateStep(i, { capture: e.target.value.split(",").map((s) => s.trim()) })} />
                    </div>
                    <div>
                      <label>Skip note (shown if this step is skipped — optional)</label>
                      <input value={step.skip_note || ""} onChange={(e) => updateStep(i, { skip_note: e.target.value })} />
                    </div>
                  </div>
                </div>
              </div>
            ))}
          </div>
          <button className="btn secondary sm" style={{ marginTop: 4 }} onClick={addStep}>+ Add step</button>

          {unresolved.length > 0 && (
            <div className="msg error" style={{ marginTop: 14 }}>
              ⚠️ These placeholders are never defined by an input or an earlier step's capture: {unresolved.map((p) => `{${p}}`).join(", ")}.
              The step will be skipped at run time until it's provided.
            </div>
          )}

          <div style={{ marginTop: 16, display: "flex", gap: 8 }}>
            <button className="btn" onClick={save} disabled={saving}>{saving ? "Saving…" : "Save"}</button>
            <button className="btn secondary" onClick={cancelEdit} disabled={saving}>Cancel</button>
          </div>

          <datalist id="system-options">{knownSystems.map((s) => <option value={s} key={s} />)}</datalist>
          <datalist id="tool-options">{tools.map((t) => <option value={t.name} key={t.name} />)}</datalist>
        </div>
      )}

      <div className="grid grid-2" style={{ marginTop: 16 }}>
        {flows.map((f) => (
          <div className="card" key={f.id}>
            <h3>{f.name} {f.is_builtin && <span className="pill">built-in</span>}</h3>
            <div style={{ fontSize: 13, color: "var(--slate)" }}>{f.description}</div>
            <div className="flow-glance" style={{ marginTop: 10, marginBottom: 0 }}>
              {f.steps.map((s, i) => (
                <span className="seg" key={i}>
                  {i > 0 && <span className="arrow">→</span>}
                  <span className="dot" style={{ background: systemColor(s.system) }} title={`${s.system}: ${s.tool}`} />
                </span>
              ))}
            </div>
            <div style={{ fontSize: 12, color: "var(--muted)", marginTop: 8 }}>{f.steps.length} steps</div>
            {canEdit && (
              <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
                <button className="btn secondary sm" onClick={() => startEdit(f)}>Edit</button>
                {!f.is_builtin && <button className="btn danger sm" onClick={() => remove(f.id)}>Delete</button>}
              </div>
            )}
          </div>
        ))}
      </div>
    </>
  );
}

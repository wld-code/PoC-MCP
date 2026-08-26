import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useLatestGuard } from "../api/useLatestGuard";
import { useAuth } from "../auth/AuthContext";

interface LlmOut { id: string; name: string; kind: string; model: string; base_url: string; has_key: boolean; is_default: boolean; needs_key: boolean }

export default function AiModels() {
  const { hasRole } = useAuth();
  const [llms, setLlms] = useState<LlmOut[]>([]);
  const [kinds, setKinds] = useState<string[]>([]);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("openai-compatible");
  const [model, setModel] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [apiKey, setApiKey] = useState("");
  const [msg, setMsg] = useState("");

  const guard = useLatestGuard();
  async function refresh() {
    await guard(() => api.get("/api/llms"), (d) => { setLlms(d.llms); setKinds(d.kinds); });
  }
  useEffect(() => { refresh(); }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setMsg("");
    try {
      await api.post("/api/llms", { name, kind, model, base_url: baseUrl, api_key: apiKey });
      setName(""); setModel(""); setBaseUrl(""); setApiKey("");
      await refresh();
    } catch (err: any) { setMsg(err.message); }
  }

  async function setDefault(id: string) { await api.put(`/api/llms/${id}/default`); await refresh(); }
  async function remove(id: string) { await api.del(`/api/llms/${id}`); await refresh(); }

  return (
    <>
      <h1>AI Models</h1>
      <p className="subtitle">The LLM registry. Reading is available to every role; adding/editing keys is admin-only.</p>

      {hasRole("admin") && (
        <div className="card">
          <h3>Add a model</h3>
          <form onSubmit={add}>
            <div className="grid grid-2">
              <div><label>Name</label><input value={name} onChange={(e) => setName(e.target.value)} required /></div>
              <div><label>Kind</label>
                <select value={kind} onChange={(e) => setKind(e.target.value)}>
                  {kinds.map((k) => <option key={k} value={k}>{k}</option>)}
                </select>
              </div>
              <div><label>Model id</label><input value={model} onChange={(e) => setModel(e.target.value)} placeholder="gpt-4o" /></div>
              <div><label>Base URL (optional)</label><input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} /></div>
            </div>
            <label>API key</label>
            <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="sk-..." />
            <button className="btn" style={{ marginTop: 12 }}>Add model</button>
          </form>
          {msg && <div className="msg error" style={{ marginTop: 10 }}>{msg}</div>}
        </div>
      )}

      <div className="card">
        <h3>Registered models</h3>
        <table>
          <thead><tr><th>Name</th><th>Kind</th><th>Model</th><th>Key</th><th>Default</th>{hasRole("admin") && <th />}</tr></thead>
          <tbody>
            {llms.map((l) => (
              <tr key={l.id}>
                <td>{l.name}</td>
                <td>{l.kind}</td>
                <td>{l.model || "(default)"}</td>
                <td>{l.needs_key ? (l.has_key ? <span className="pill ok">set</span> : <span className="pill error">missing</span>) : "—"}</td>
                <td>{l.is_default ? <span className="pill role-operator">default</span> : hasRole("admin") && <button className="btn secondary sm" onClick={() => setDefault(l.id)}>Set default</button>}</td>
                {hasRole("admin") && <td>{!l.is_default && <button className="btn danger sm" onClick={() => remove(l.id)}>Delete</button>}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

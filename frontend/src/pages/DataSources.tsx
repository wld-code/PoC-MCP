import { useEffect, useState } from "react";
import { api } from "../api/client";

interface McpServer { id: string; name: string; url: string; status: string; error: string | null; tool_count: number }

export default function DataSources() {
  const [servers, setServers] = useState<McpServer[]>([]);
  const [url, setUrl] = useState("");
  const [id, setId] = useState("");
  const [msg, setMsg] = useState("");

  async function refresh() { setServers((await api.get("/api/mcp/servers")).servers); }
  useEffect(() => { refresh(); }, []);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setMsg("");
    try {
      await api.post("/api/mcp/servers", { url, id: id || undefined });
      setUrl(""); setId("");
      await refresh();
    } catch (err: any) { setMsg(err.message); }
  }

  async function remove(sid: string) {
    await api.del(`/api/mcp/servers/${sid}`);
    await refresh();
  }

  return (
    <>
      <h1>Data Sources</h1>
      <p className="subtitle">Manage the MCP servers the agent connects to — add/remove at runtime, no restart.</p>

      <div className="card">
        <h3>Connect a new MCP server</h3>
        <form onSubmit={add}>
          <label>URL</label>
          <input value={url} onChange={(e) => setUrl(e.target.value)} placeholder="http://cvc-mcp:8012/mcp" required />
          <label>Id (optional)</label>
          <input value={id} onChange={(e) => setId(e.target.value)} placeholder="auto-generated from the URL" />
          <button className="btn" style={{ marginTop: 12 }}>Connect</button>
        </form>
        {msg && <div className="msg error" style={{ marginTop: 10 }}>{msg}</div>}
      </div>

      <div className="card">
        <h3>Connected servers</h3>
        <table>
          <thead><tr><th>Id</th><th>Name</th><th>URL</th><th>Status</th><th>Tools</th><th /></tr></thead>
          <tbody>
            {servers.map((s) => (
              <tr key={s.id}>
                <td>{s.id}</td>
                <td>{s.name}</td>
                <td style={{ fontSize: 12 }}>{s.url}</td>
                <td><span className={`pill ${s.status === "connected" ? "ok" : "error"}`}>{s.status}</span>{s.error && <div style={{ fontSize: 11, color: "var(--danger)" }}>{s.error}</div>}</td>
                <td>{s.tool_count}</td>
                <td><button className="btn danger sm" onClick={() => remove(s.id)}>Remove</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

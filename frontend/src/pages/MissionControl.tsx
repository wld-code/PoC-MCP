import { useEffect, useState } from "react";
import { api } from "../api/client";
import { EvidencePanel } from "../components/EvidencePanel";

interface ChatOut { answer: string; tool_calls: { name: string; arguments: Record<string, unknown> }[]; error: boolean; provider: string; model: string }
interface LlmOut { id: string; name: string; has_key: boolean; needs_key: boolean }

function sessionId(): string {
  const key = "aiops_chat_session";
  let sid = sessionStorage.getItem(key);
  if (!sid) {
    sid = crypto.randomUUID();
    sessionStorage.setItem(key, sid);
  }
  return sid;
}

export default function MissionControl() {
  const [llms, setLlms] = useState<LlmOut[]>([]);
  const [provider, setProvider] = useState<string>("");
  const [question, setQuestion] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<ChatOut | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.get("/api/llms").then((d) => {
      setLlms(d.llms);
      setProvider(d.default || d.llms[0]?.id || "");
    }).catch(() => {});
  }, []);

  async function ask(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;
    setBusy(true);
    setError("");
    try {
      const res = await api.post("/api/chat", { session_id: sessionId(), message: question.trim(), provider });
      setResult(res);
      setQuestion("");
    } catch (err: any) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Mission Control</h1>
      <p className="subtitle">Ask the agent a question in natural language and get an executive answer backed by an evidence panel.</p>

      <div className="card">
        <label>Agent (LLM)</label>
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          {llms.map((l) => (
            <option key={l.id} value={l.id}>{l.name}{l.needs_key && !l.has_key ? " (no key set)" : ""}</option>
          ))}
        </select>
        <form onSubmit={ask}>
          <label>Investigation</label>
          <textarea rows={3} value={question} onChange={(e) => setQuestion(e.target.value)}
                     placeholder="Is Walid's car online, and what services are active on it?" />
          <button className="btn" style={{ marginTop: 12 }} disabled={busy}>{busy ? "Investigating…" : "Ask"}</button>
        </form>
      </div>

      {error && <div className="msg error">{error}</div>}

      {result && (
        <div className="card">
          <h3>Executive Answer {result.error && <span className="pill error">error</span>}</h3>
          <div className="chat-answer">{result.answer}</div>
          <EvidencePanel toolCalls={result.tool_calls} />
        </div>
      )}
    </>
  );
}

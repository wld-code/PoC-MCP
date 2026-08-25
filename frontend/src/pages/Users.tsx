import { useEffect, useState } from "react";
import { api } from "../api/client";
import { useAuth, Role } from "../auth/AuthContext";

interface UserOut { id: number; email: string; role: Role; is_active: boolean; created_at: string }

export default function Users() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<UserOut[]>([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<Role>("viewer");
  const [msg, setMsg] = useState("");

  async function refresh() { setUsers(await api.get("/api/users")); }
  useEffect(() => { refresh(); }, []);

  async function create(e: React.FormEvent) {
    e.preventDefault();
    setMsg("");
    try {
      await api.post("/api/users", { email, password, role });
      setEmail(""); setPassword(""); setRole("viewer");
      await refresh();
    } catch (err: any) { setMsg(err.message); }
  }

  async function setRoleFor(u: UserOut, newRole: Role) { await api.put(`/api/users/${u.id}`, { role: newRole }); await refresh(); }
  async function toggleActive(u: UserOut) { await api.put(`/api/users/${u.id}`, { is_active: !u.is_active }); await refresh(); }
  async function remove(id: number) { await api.del(`/api/users/${id}`); await refresh(); }

  return (
    <>
      <h1>Users</h1>
      <p className="subtitle">Admin-only: create accounts, assign roles, deactivate or remove.</p>

      <div className="card">
        <h3>New user</h3>
        <form onSubmit={create}>
          <div className="grid grid-3">
            <div><label>Email</label><input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></div>
            <div><label>Password</label><input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} /></div>
            <div><label>Role</label>
              <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
                <option value="viewer">viewer</option>
                <option value="operator">operator</option>
                <option value="admin">admin</option>
              </select>
            </div>
          </div>
          <button className="btn" style={{ marginTop: 12 }}>Create user</button>
        </form>
        {msg && <div className="msg error" style={{ marginTop: 10 }}>{msg}</div>}
      </div>

      <div className="card">
        <h3>Accounts</h3>
        <table>
          <thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Created</th><th /></tr></thead>
          <tbody>
            {users.map((u) => (
              <tr key={u.id}>
                <td>{u.email}{u.id === me?.id && " (you)"}</td>
                <td>
                  <select value={u.role} disabled={u.id === me?.id} onChange={(e) => setRoleFor(u, e.target.value as Role)}>
                    <option value="viewer">viewer</option>
                    <option value="operator">operator</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
                <td><span className={`pill ${u.is_active ? "ok" : "error"}`}>{u.is_active ? "active" : "disabled"}</span></td>
                <td>{new Date(u.created_at).toLocaleDateString()}</td>
                <td>
                  {u.id !== me?.id && (
                    <>
                      <button className="btn secondary sm" onClick={() => toggleActive(u)}>{u.is_active ? "Disable" : "Enable"}</button>{" "}
                      <button className="btn danger sm" onClick={() => remove(u.id)}>Delete</button>
                    </>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}

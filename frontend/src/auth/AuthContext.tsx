import { createContext, useContext, useEffect, useState, ReactNode, useCallback } from "react";
import { api, refreshAccessToken, setAccessToken, setOnAuthFailure } from "../api/client";

export type Role = "admin" | "operator" | "viewer";
export interface User { id: number; email: string; role: Role; is_active: boolean; created_at: string }

interface AuthState {
  user: User | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  hasRole: (min: Role) => boolean;
}

const RANK: Record<Role, number> = { viewer: 0, operator: 1, admin: 2 };

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(true);

  const clear = useCallback(() => {
    setAccessToken(null);
    setUser(null);
  }, []);

  useEffect(() => {
    setOnAuthFailure(clear);
    (async () => {
      // On page load there's no access token in memory (never persisted) —
      // try the refresh cookie to silently resume a session.
      const token = await refreshAccessToken();
      if (token) {
        try {
          setUser(await api.get("/api/auth/me"));
        } catch {
          clear();
        }
      }
      setLoading(false);
    })();
  }, [clear]);

  const login = useCallback(async (email: string, password: string) => {
    const form = new URLSearchParams();
    form.set("username", email);
    form.set("password", password);
    const res = await fetch("/api/auth/login", { method: "POST", body: form, credentials: "include" });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new Error((data && data.detail) || "Login failed");
    }
    const data = await res.json();
    setAccessToken(data.access_token);
    setUser(await api.get("/api/auth/me"));
  }, []);

  const logout = useCallback(async () => {
    try { await api.post("/api/auth/logout"); } catch { /* ignore */ }
    clear();
  }, [clear]);

  const hasRole = useCallback((min: Role) => !!user && RANK[user.role] >= RANK[min], [user]);

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, hasRole }}>
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used within AuthProvider");
  return ctx;
}

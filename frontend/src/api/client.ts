// Fetch wrapper: attaches the in-memory access token, retries once through a
// refresh (via the httpOnly cookie) on a 401, and never puts the access token
// anywhere JS-persistent (no localStorage) — see auth/AuthContext.tsx.

let accessToken: string | null = null;
let onAuthFailure: (() => void) | null = null;

export function setAccessToken(token: string | null) {
  accessToken = token;
}

export function setOnAuthFailure(fn: () => void) {
  onAuthFailure = fn;
}

async function refreshAccessToken(): Promise<string | null> {
  const res = await fetch("/api/auth/refresh", { method: "POST", credentials: "include" });
  if (!res.ok) return null;
  const data = await res.json();
  accessToken = data.access_token;
  return accessToken;
}

export async function apiFetch(path: string, options: RequestInit = {}, retry = true): Promise<any> {
  const headers = new Headers(options.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");

  const res = await fetch(path, { ...options, headers, credentials: "include" });

  if (res.status === 401 && retry) {
    const fresh = await refreshAccessToken();
    if (fresh) return apiFetch(path, options, false);
    onAuthFailure?.();
    throw new Error("Session expired — please log in again.");
  }

  const isJson = res.headers.get("content-type")?.includes("application/json");
  const data = isJson ? await res.json().catch(() => null) : null;
  if (!res.ok) {
    throw new Error((data && (data.detail as string)) || `HTTP ${res.status}`);
  }
  return data;
}

export const api = {
  get: (path: string) => apiFetch(path),
  post: (path: string, body?: unknown) => apiFetch(path, { method: "POST", body: body ? JSON.stringify(body) : undefined }),
  put: (path: string, body?: unknown) => apiFetch(path, { method: "PUT", body: body ? JSON.stringify(body) : undefined }),
  del: (path: string) => apiFetch(path, { method: "DELETE" }),
};

export { refreshAccessToken };

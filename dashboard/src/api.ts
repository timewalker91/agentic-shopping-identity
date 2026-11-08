// Talks only to the Aide-commerce Control API. The browser never calls AWS directly.

export const API_URL: string = import.meta.env.VITE_API_URL ?? "http://127.0.0.1:8000";

export type Budget = { max: number; spent: number; held: number; remaining: number; currency: string };

export type Passport = {
  iss: string;
  sub: string;
  aud: string;
  act: { sub: string; sub_profile: string };
  agent_id: string;
  sid: string;
  mandate_id: string;
  tool_profile: string;
  merchant: string;
  ship_to_id: string;
  scope: string;
  jti: string;
  iat: number;
  exp: number;
};

export type Session = {
  sid: string;
  user: string;
  username: string;
  mandate_id: string;
  agent_id: string;
  merchant: string;
  status: "active" | "revoked" | string;
  created_at: string;
  passport: Passport | null;
  token_issued_at?: string;
  revoked_at?: string;
  revoke_reason?: string;
  budget?: Budget;
};

export type Mandate = Budget & {
  mandate_id: string;
  merchant: string;
  ship_to_id: string;
  status: string;
};

export type AuditEvent = {
  ts: string;
  id: string;
  sid: string;
  agent_id: string;
  user: string;
  tool: string;
  decision: "allow" | "deny" | "revoked";
  reason: string;
  layer: string;
  latency_ms: number;
};

export type Me = { user: { sub: string; username: string }; mandates: Mandate[]; sessions: Session[] };

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

const TOKEN_KEY = "aide.id_token";

export function savedToken(): string | null {
  try {
    return sessionStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

function saveToken(token: string | null): void {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token);
    else sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode: keep the token in memory only */
  }
}

async function request<T>(path: string, token: string | null, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  let resp: Response;
  try {
    resp = await fetch(`${API_URL}${path}`, { ...init, headers: { ...headers, ...(init.headers ?? {}) } });
  } catch {
    throw new ApiError(0, `Can't reach the Aide-commerce API at ${API_URL}. Is it running?`);
  }
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new ApiError(resp.status, body.message ?? body.error ?? `HTTP ${resp.status}`);
  return body as T;
}

export async function login(username: string, password: string): Promise<string> {
  const data = await request<{ id_token: string }>("/auth/login", null, {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  saveToken(data.id_token);
  return data.id_token;
}

export function logout(): void {
  saveToken(null);
}

export const api = {
  me: (t: string) => request<Me>("/me", t),
  session: (t: string, sid: string) => request<Session>(`/sessions/${sid}`, t),
  audit: (t: string, sid: string, after?: string) =>
    request<{ events: AuditEvent[]; cursor?: string }>(
      `/sessions/${sid}/audit${after ? `?after=${encodeURIComponent(after)}` : ""}`,
      t,
    ),
  stop: (t: string, sid: string) => request<Session>(`/sessions/${sid}/stop`, t, { method: "POST" }),
  reset: (t: string) => request<{ ok: boolean }>("/demo/reset", t, { method: "POST" }),
};

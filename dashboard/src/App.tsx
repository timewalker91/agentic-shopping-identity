import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, AuditEvent, login, logout, Me, savedToken, Session } from "./api";
import { PassportCard } from "./components/Passport";
import { Feed } from "./components/Feed";
import { BudgetGauge, Stats, StopButton } from "./components/Controls";

function Login({ onToken }: { onToken: (t: string) => void }) {
  const [username, setUsername] = useState("parent");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      onToken(await login(username, password));
    } catch (err) {
      setError(err instanceof ApiError && err.status === 401 ? "Wrong username or password." : String((err as Error).message));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="login">
      <form className="login-card" onSubmit={submit}>
        <p className="eyebrow">Aide-commerce</p>
        <h1>Agent security console</h1>
        <p className="login-lede">Sign in as the parent to watch and control your shopping agent.</p>
        <label>
          Username
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label>
          Password
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="primary" disabled={busy || !password}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </main>
  );
}

function usePoll(fn: () => void | Promise<void>, ms: number, enabled: boolean) {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    const tick = async () => {
      if (!alive) return;
      try {
        await ref.current();
      } finally {
        if (alive) timer = window.setTimeout(tick, ms);
      }
    };
    let timer = window.setTimeout(tick, 0);
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [ms, enabled]);
}

export default function App() {
  const [token, setToken] = useState<string | null>(savedToken());
  const [me, setMe] = useState<Me | null>(null);
  const [pinned, setPinned] = useState<string | null>(null); // null = follow the newest session
  const [session, setSession] = useState<Session | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [now, setNow] = useState(Date.now());
  const [stopping, setStopping] = useState(false);
  const cursor = useRef<string | undefined>(undefined);
  const currentSid = useRef<string | null>(null);

  const sid = pinned ?? me?.sessions[0]?.sid ?? null;

  const handleError = useCallback((err: unknown) => {
    if (err instanceof ApiError && err.status === 401) {
      logout();
      setToken(null);
      return;
    }
    setError(err instanceof Error ? err.message : String(err));
  }, []);

  // Newest sessions (the agent CLI creates them); every 2 s.
  usePoll(async () => {
    try {
      setMe(await api.me(token!));
      setError("");
    } catch (err) {
      handleError(err);
    }
  }, 2000, !!token);

  // Switching sessions resets the feed.
  useEffect(() => {
    if (sid !== currentSid.current) {
      currentSid.current = sid;
      cursor.current = undefined;
      setEvents([]);
      setSession(null);
    }
  }, [sid]);

  // Session + new audit events; every second.
  usePoll(async () => {
    if (!token || !sid) return;
    try {
      const [s, a] = await Promise.all([api.session(token, sid), api.audit(token, sid, cursor.current)]);
      if (sid !== currentSid.current) return;
      setSession(s);
      if (a.events.length) {
        cursor.current = a.cursor ?? a.events[a.events.length - 1].ts;
        setEvents((prev) => {
          const seen = new Set(prev.map((e) => e.id));
          return [...prev, ...a.events.filter((e) => !seen.has(e.id))];
        });
        setFresh(new Set(a.events.map((e) => e.id)));
      }
    } catch (err) {
      handleError(err);
    }
  }, 1000, !!token && !!sid);

  // Clock for the passport countdown.
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 250);
    return () => window.clearInterval(t);
  }, []);

  async function stop() {
    if (!token || !sid) return;
    setStopping(true);
    try {
      setSession(await api.stop(token, sid));
    } catch (err) {
      handleError(err);
    } finally {
      setStopping(false);
    }
  }

  async function reset() {
    if (!token) return;
    try {
      await api.reset(token);
      setPinned(null);
      setMe(await api.me(token));
    } catch (err) {
      handleError(err);
    }
  }

  if (!token) return <Login onToken={setToken} />;

  return (
    <div className="console">
      <header className="topbar">
        <div className="brand">
          <img className="brand-mark" src="/logo-mark.svg" alt="" />
          <span className="brand-name">Aide-commerce</span>
          <span className="brand-sub">Agent security console</span>
        </div>
        <div className="topbar-right">
          {me && me.sessions.length > 0 && (
            <label className="session-pick">
              Session
              <select value={pinned ?? ""} onChange={(e) => setPinned(e.target.value || null)}>
                <option value="">Follow newest</option>
                {me.sessions.map((s) => (
                  <option key={s.sid} value={s.sid}>
                    {s.sid} · {s.status}
                  </option>
                ))}
              </select>
            </label>
          )}
          <span className="who">{me?.user.username ?? ""}</span>
          <button className="ghost" onClick={reset}>Reset demo</button>
          <button className="ghost" onClick={() => { logout(); setToken(null); }}>Sign out</button>
        </div>
      </header>

      {error && <p className="banner" role="alert">{error}</p>}

      {!session ? (
        <main className="waiting">
          <h2>Waiting for the agent</h2>
          <p>
            Start a shopping session from the agent: <code>agent session start</code>. It appears here
            automatically.
          </p>
        </main>
      ) : (
        <main className="grid">
          <div className="col-left">
            <PassportCard session={session} now={now} />
            <div className="controls">
              <BudgetGauge budget={session.budget} />
              <StopButton session={session} onStop={stop} busy={stopping} />
            </div>
          </div>
          <div className="col-right">
            <Stats session={session} events={events} />
            <Feed events={events} freshIds={fresh} />
          </div>
        </main>
      )}
    </div>
  );
}

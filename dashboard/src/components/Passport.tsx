import type { Session } from "../api";

function mrzField(value: string, width: number): string {
  const clean = value.toUpperCase().replace(/[^A-Z0-9]/g, "<");
  return (clean + "<".repeat(width)).slice(0, width);
}

/** Passport-style machine-readable zone built from the token's own claims. */
function mrzLines(s: Session): [string, string] {
  const p = s.passport;
  const agent = p?.act.sub ?? s.agent_id;
  const line1 = mrzField(`AP<AIDE<${agent}<<${s.username}`, 44);
  const line2 = mrzField(
    `${p?.sid ?? s.sid}<${p?.mandate_id ?? s.mandate_id}<${p?.merchant ?? s.merchant}<${p?.ship_to_id ?? ""}`,
    44,
  );
  return [line1, line2];
}

function mmss(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function PassportCard({ session, now }: { session: Session; now: number }) {
  const p = session.passport;
  const revoked = session.status !== "active";
  const lifetime = p ? p.exp - p.iat : 300;
  const left = p ? p.exp - now / 1000 : 0;
  const [mrz1, mrz2] = mrzLines(session);

  return (
    <section className={`passport ${revoked ? "is-revoked" : ""}`} aria-label="Agent Passport">
      <header className="passport-head">
        <span className="passport-issuer">Aide-commerce</span>
        <span className="passport-title">Agent Passport</span>
      </header>

      <div className="passport-body">
        <div className="passport-seal" aria-hidden="true">
          <svg viewBox="0 0 64 64">
            <circle cx="32" cy="32" r="29" />
            <circle cx="32" cy="32" r="21" />
            <path d="M20 40 L32 18 L44 40 Z" />
            <path d="M24 34 H40" />
          </svg>
        </div>
        <dl className="passport-fields">
          <div>
            <dt>Holder (agent)</dt>
            <dd className="mono">{p?.act.sub ?? session.agent_id}</dd>
          </div>
          <div>
            <dt>Acting for</dt>
            <dd>{session.username}</dd>
          </div>
          <div>
            <dt>Mandate</dt>
            <dd className="mono">
              {p?.mandate_id ?? session.mandate_id} · {p?.merchant ?? session.merchant}
            </dd>
          </div>
          <div>
            <dt>Ships only to</dt>
            <dd className="mono">{p?.ship_to_id ?? "–"}</dd>
          </div>
          <div className="span-2">
            <dt>Allowed to</dt>
            <dd className="scopes">
              {(p?.scope ?? "").split(" ").filter(Boolean).map((sc) => (
                <span key={sc} className="scope">
                  {sc}
                </span>
              ))}
              {!p && <span className="muted">no passport issued yet</span>}
            </dd>
          </div>
        </dl>
      </div>

      <div className="passport-validity">
        <div className="validity-label">
          {p ? (left > 0 ? "Token valid for" : "Token expired") : "Waiting for the agent to request a passport"}
        </div>
        {p && (
          <>
            <div className="validity-clock mono">{mmss(left)}</div>
            <div className="validity-bar" aria-hidden="true">
              <span style={{ width: `${Math.max(0, Math.min(100, (left / lifetime) * 100))}%` }} />
            </div>
          </>
        )}
      </div>

      <div className="mrz mono" aria-label="Machine-readable zone">
        <div>{mrz1}</div>
        <div>{mrz2}</div>
      </div>

      {revoked && (
        <div className="stamp" role="status">
          <span className="stamp-word">Revoked</span>
          <span className="stamp-note">
            {session.revoke_reason?.startsWith("auto_revoke") ? "automatically · policy violations" : "by the parent"}
          </span>
        </div>
      )}
    </section>
  );
}

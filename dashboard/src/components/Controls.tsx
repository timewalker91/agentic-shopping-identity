import type { AuditEvent, Budget, Session } from "../api";
import { isAgentCall } from "../labels";

export function BudgetGauge({ budget }: { budget?: Budget }) {
  if (!budget) return null;
  const pct = (v: number) => `${Math.max(0, Math.min(100, (v / budget.max) * 100))}%`;
  return (
    <section className="budget" aria-label="Budget">
      <header className="panel-head">
        <h2>Budget</h2>
        <span className="panel-sub">Enforced on the server, not by the AI</span>
      </header>
      <div className="budget-numbers">
        <span className="big mono">${budget.spent.toFixed(2)}</span>
        <span className="of">spent of ${budget.max.toFixed(2)}</span>
      </div>
      <div className="gauge" aria-hidden="true">
        <span className="g-spent" style={{ width: pct(budget.spent) }} />
        <span className="g-held" style={{ width: pct(budget.held) }} />
      </div>
      <div className="budget-legend">
        <span>${budget.remaining.toFixed(2)} left</span>
        {budget.held > 0 && <span className="held">${budget.held.toFixed(2)} on hold</span>}
      </div>
    </section>
  );
}

export function StopButton({ session, onStop, busy }: { session: Session; onStop: () => void; busy: boolean }) {
  const active = session.status === "active";
  return (
    <button className="stop" onClick={onStop} disabled={!active || busy}>
      <span className="stop-icon" aria-hidden="true" />
      <span>{active ? (busy ? "Stopping…" : "Stop agent") : "Agent stopped"}</span>
    </button>
  );
}

function seconds(ms: number): string {
  return `${(ms / 1000).toFixed(1)} s`;
}

/** Amount of a paid checkout, from the gateway's audit reason ("paid $15.49 ..."); 0 for anything else. */
function paidAmount(e: AuditEvent): number {
  if (e.tool !== "checkout" || e.decision !== "allow") return 0;
  const m = /^paid \$([\d,]+(?:\.\d+)?)/.exec(e.reason);
  return m ? Number(m[1].replace(/,/g, "")) : 0;
}

function usd(n: number): string {
  return n === 0 ? "$0" : `$${n.toFixed(2)}`;
}

/** Counts plus the revocation proof: how fast the next call was refused, and how long the token still had. */
export function Stats({ session, events }: { session: Session; events: AuditEvent[] }) {
  const calls = events.filter(isAgentCall);
  const allowed = calls.filter((e) => e.decision === "allow").length;
  const blocked = calls.filter((e) => e.decision === "deny").length;
  const revokedAt = session.revoked_at ? Date.parse(session.revoked_at) : null;
  const refused =
    revokedAt != null
      ? calls.find((e) => e.decision === "revoked" && e.layer === "revocation" && Date.parse(e.ts) >= revokedAt)
      : undefined;
  const exp = session.passport?.exp;
  // Money: once a policy rule has blocked the agent (an attack, not a budget limit), anything paid
  // after that point counts as lost. Purchases before it were within the mandate.
  const firstAttack = calls.find((e) => e.decision === "deny" && e.layer === "policy");
  const paidSince = (from?: AuditEvent) =>
    calls.filter((e) => !from || Date.parse(e.ts) > Date.parse(from.ts)).reduce((sum, e) => sum + paidAmount(e), 0);
  const lost = firstAttack ? paidSince(firstAttack) : 0;

  return (
    <section className="stats" aria-label="Session summary">
      <div className="stat">
        <span className="stat-n mono">{allowed}</span>
        <span className="stat-l">calls allowed</span>
      </div>
      <div className="stat">
        <span className={`stat-n mono ${blocked ? "red" : ""}`}>{blocked}</span>
        <span className="stat-l">calls blocked by policy</span>
      </div>
      <div className="stat money">
        {firstAttack ? (
          <>
            <span className={`stat-n mono ${lost ? "red" : "green"}`}>{usd(lost)}</span>
            <span className="stat-l">{lost ? "paid after the attack started" : "lost to the attack: no order went through after it started"}</span>
          </>
        ) : (
          <>
            <span className="stat-n mono">{usd(paidSince())}</span>
            <span className="stat-l">spent within the mandate</span>
          </>
        )}
      </div>
      <div className="stat wide">
        {revokedAt == null ? (
          <>
            <span className="stat-n mono green">Active</span>
            <span className="stat-l">agent can act within its mandate</span>
          </>
        ) : refused && exp ? (
          <>
            <span className="stat-n mono red">{Math.max(0, Math.round(exp - Date.parse(refused.ts) / 1000))} s</span>
            <span className="stat-l">
              still left on the token when its next call was refused: revocation doesn't wait for expiry
              (refused {seconds(Date.parse(refused.ts) - revokedAt)} after the revoke)
            </span>
          </>
        ) : (
          <>
            <span className="stat-n mono red">Revoked</span>
            <span className="stat-l">every further call will be refused</span>
          </>
        )}
      </div>
    </section>
  );
}

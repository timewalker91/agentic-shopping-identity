import type { AuditEvent } from "../api";
import { clock, plainReason, toolName } from "../labels";

const BADGE: Record<string, string> = { allow: "Allowed", deny: "Blocked", revoked: "Revoked" };

export function Feed({ events, freshIds }: { events: AuditEvent[]; freshIds: Set<string> }) {
  const rows = [...events].reverse();
  return (
    <section className="feed" aria-label="Live decisions">
      <header className="panel-head">
        <h2>Live decisions</h2>
        <span className="panel-sub">Every tool call the agent makes passes the gateway first</span>
      </header>
      {rows.length === 0 ? (
        <p className="empty">
          No tool calls yet. Start the agent with <code>agent session start</code>, then <code>agent shop</code>.
        </p>
      ) : (
        <ol className="feed-list">
          {rows.map((e) => (
            <li key={e.id} className={`row d-${e.decision} ${freshIds.has(e.id) ? "fresh" : ""}`}>
              <span className="row-time mono">{clock(e.ts)}</span>
              <span className={`badge b-${e.decision}`}>{BADGE[e.decision] ?? e.decision}</span>
              <span className="row-tool">{toolName(e.tool)}</span>
              <span className="row-reason">
                {plainReason(e)}
                <span className="row-raw mono">
                  {e.layer} · {e.reason}
                </span>
              </span>
              <span className="row-ms mono">{e.latency_ms ? `${Math.round(e.latency_ms)} ms` : ""}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

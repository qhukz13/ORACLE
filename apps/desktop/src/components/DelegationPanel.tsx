/**
 * Live delegations — docs/INTEGRATIONS.md §7's "present: diff + test results +
 * summary + cost", as a panel folded purely from `task.*` and `delegate.event`.
 *
 * Two rules carried over from the rest of the UI: nothing optimistic (the discard
 * button sends a command; the card keeps its workspace line until the server says
 * otherwise), and verification is shown as ORACLE's evidence — the test verdict comes
 * from the gate-run `dev.run_tests`, and when no verifier ran the card says so
 * instead of implying success.
 */

import type { Delegation } from "../protocol";
import { asRecord, num, str } from "../protocol";

const STATE_LABEL: Record<string, string> = {
  created: "STARTING",
  rendering: "BUILDING PACKET",
  awaiting_egress: "NEEDS YOU — egress preview",
  running: "RUNNING",
  verifying: "VERIFYING",
  finished: "DONE",
};

export interface DelegationPanelProps {
  delegations: Delegation[];
  onDiscard(taskId: string): void;
}

function feedLine(kind: string, text: string, tool: string | null): string {
  if (kind === "tool_use") return `⚒ ${tool ?? "?"}`;
  if (kind === "started") return `▶ started${text ? ` (${text})` : ""}`;
  if (kind === "finished") return "■ finished";
  if (kind === "error") return `✖ ${text || "error"}`;
  return text;
}

function Verdict({ result }: { result: Record<string, unknown> }) {
  const tests = asRecord(result["tests"]);
  if (tests["ran"] !== true) {
    return <span className="dg-tests warn">tests: not verified ({str(tests["reason"])})</span>;
  }
  const failed = num(tests["failed"], 0);
  return (
    <span className={`dg-tests ${failed > 0 ? "bad" : "ok"}`}>
      tests: {num(tests["passed"], 0)} passed, {failed} failed
    </span>
  );
}

export function DelegationPanel({ delegations, onDiscard }: DelegationPanelProps) {
  if (delegations.length === 0) return null;
  return (
    <section className="delegations" aria-label="Delegations">
      {delegations.map((d) => {
        const result = asRecord(d.result ?? {});
        const workspace = str(result["workspace"]);
        const cost = result["cost_usd"];
        return (
          <article key={d.taskId} className={`dg dg-${d.outcome ?? d.state}`}>
            <header className="dg-head">
              <span className="dg-adapter">{d.adapter || "delegate"}</span>
              <span className="dg-task">{d.task}</span>
              <span className="spacer" />
              <span className="dg-state">
                {d.outcome ? d.outcome.toUpperCase() : (STATE_LABEL[d.state] ?? d.state)}
              </span>
            </header>

            {d.feed.length > 0 && (
              <ul className="dg-feed">
                {d.feed.slice(-8).map((e, i) => (
                  <li key={i} className={e.fromSubagent ? "sub" : undefined}>
                    {feedLine(e.kind, e.text, e.tool)}
                  </li>
                ))}
              </ul>
            )}

            {d.outcome === "fallback" && (
              <p className="dg-fallback">
                No agent available — the packet was written to{" "}
                <code>{str(result["packet_dir"])}</code>. {str(result["explanation"])}
              </p>
            )}

            {d.state === "finished" && d.outcome !== "fallback" && (
              <footer className="dg-result">
                <span>{num(result["diff_lines"], 0)} diff lines</span>
                <Verdict result={result} />
                {typeof cost === "number" && <span>${cost.toFixed(4)}</span>}
                {workspace && (
                  <>
                    <code className="dg-ws">{workspace}</code>
                    <button className="ghost" onClick={() => onDiscard(d.taskId)}>
                      Discard worktree
                    </button>
                  </>
                )}
              </footer>
            )}
          </article>
        );
      })}
    </section>
  );
}

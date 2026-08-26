/**
 * The Confirmation Center — docs/UI.md#9-confirmation-center.
 *
 * The most safety-critical surface in the product, and the one place where the UI is
 * *part of* the security model rather than a view onto it. A confirmation the user
 * cannot read is a rubber stamp, and a rubber stamp is worse than no prompt at all:
 * it trains the habit of clicking Approve.
 *
 * The rules it implements, each of which exists because of a specific way this goes
 * wrong:
 *
 * - **Show the real action, never a paraphrase.** The arguments are rendered verbatim
 *   and monospaced, straight from the event. Nothing here is re-worded by a model.
 * - **Approve is never the default focus**, and a guard window blocks an Enter left
 *   over from the previous action. Accidental approval is the failure mode that
 *   matters; accidental denial costs one retry.
 * - **T3 requires typing the confirmation phrase.** A destructive action should be
 *   inconvenient in proportion to being destructive.
 * - **The provenance line is mandatory when the turn is tainted.** It is the single
 *   most useful signal for spotting a prompt-injection attempt
 *   (docs/SECURITY.md#6-prompt-injection-and-taint-tracking).
 * - **Expiry is visible and real.** The server refuses an expired approval, so a
 *   countdown that kept ticking past zero would be lying about what the button does.
 * - **Queued approvals stack and are decided one at a time.** There is no "approve
 *   all", by design.
 */

import { useEffect, useMemo, useRef, useState } from "react";
import type { Approval } from "../protocol";
import { EgressPreview } from "./EgressPreview";
import { GraphCard } from "./GraphCard";
import { PipelineCard } from "./PipelineCard";

/** Blocks an Enter keystroke carried over from whatever the user was doing before. */
const GUARD_MS = 500;
/** A destructive action should cost a moment's attention before it is possible. */
const T3_COOLDOWN_MS = 3000;

export interface ConfirmationCenterProps {
  approvals: Approval[];
  decided: Approval[];
  onRespond(approvalId: string, approve: boolean): void;
}

function useCountdown(approval: Approval | undefined): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!approval) return;
    const t = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(t);
  }, [approval?.approvalId]);
  if (!approval) return 0;
  const elapsed = (now - approval.issuedAt) / 1000;
  return Math.max(0, Math.round(approval.expiresInSec - elapsed));
}

function formatArgs(args: Record<string, unknown>): string {
  // One `key: value` per line, values verbatim. Deliberately not JSON.stringify of the
  // whole object: the user is reading a path and a message, not a data structure.
  return Object.entries(args)
    .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
    .join("\n");
}

export function ConfirmationCenter({ approvals, decided, onRespond }: ConfirmationCenterProps) {
  const current = approvals[0];
  const remaining = useCountdown(current);
  const [phrase, setPhrase] = useState("");
  const [armedAt, setArmedAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const denyRef = useRef<HTMLButtonElement>(null);

  const isT3 = current?.tier === "T3";
  const requiredPhrase = current?.tool ?? "";

  useEffect(() => {
    setPhrase("");
    setArmedAt(Date.now());
    // Focus DENY, never Approve. The safe action is the one under the cursor.
    denyRef.current?.focus();
  }, [current?.approvalId]);

  useEffect(() => {
    if (!current) return;
    const t = window.setInterval(() => setNow(Date.now()), 200);
    return () => window.clearInterval(t);
  }, [current?.approvalId]);

  const sinceArmed = now - armedAt;
  const guardOver = sinceArmed >= GUARD_MS;
  const cooldownOver = !isT3 || sinceArmed >= T3_COOLDOWN_MS;
  const phraseOk = !isT3 || phrase.trim() === requiredPhrase;
  const expired = remaining <= 0;
  const canApprove = Boolean(current) && guardOver && cooldownOver && phraseOk && !expired;

  useEffect(() => {
    if (!current) return;
    const onKey = (e: KeyboardEvent) => {
      // Letters, not Enter. Enter is what gets pressed by accident.
      if (e.key === "a" && canApprove && !isT3) {
        e.preventDefault();
        onRespond(current.approvalId, true);
      } else if (e.key === "d" || e.key === "Escape") {
        e.preventDefault();
        onRespond(current.approvalId, false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [current?.approvalId, canApprove, isT3, onRespond]);

  const lastDecided = decided[0];

  /**
   * EFFECT has two halves and they answer different questions.
   *
   * `summary` is the contract's own `side_effects` sentence: what KIND of thing is
   * about to happen. `detail` is the dry run: what THIS one would actually do — the
   * commits that would be published, the files that would be deleted. The second is
   * the one docs/UI.md#9 insists on, because a description is not a preview.
   */
  const effect = useMemo(() => {
    const preview = current?.preview ?? {};
    const summary = typeof preview["summary"] === "string" ? preview["summary"] : "";
    const raw = preview["detail"];
    const detail = Array.isArray(raw)
      ? raw.map(String).join("\n")
      : typeof raw === "string"
        ? raw
        : "";
    return { summary, detail };
  }, [current]);

  if (!current) {
    if (!lastDecided) return null;
    return (
      <section className="approvals resolved" aria-label="Last approval">
        <div className="ap-head">
          <span className="ap-icon" aria-hidden="true">
            ✓
          </span>
          <span>
            {lastDecided.tool} — {lastDecided.resolution}
          </span>
        </div>
      </section>
    );
  }

  return (
    <section
      className={`approvals tier-${current.tier.toLowerCase()}`}
      role="alertdialog"
      aria-labelledby="ap-title"
      aria-describedby="ap-args"
    >
      <div className="ap-head">
        <span className="ap-icon" aria-hidden="true">
          ⚠
        </span>
        <strong id="ap-title">APPROVAL REQUIRED</strong>
        <span className="spacer" />
        <span className="ap-tier">{current.tier}</span>
        {approvals.length > 1 && (
          <span className="ap-queue" title="decided one at a time">
            +{approvals.length - 1} queued
          </span>
        )}
      </div>

      <p className="ap-lead">ORACLE wants to run:</p>
      <pre className="ap-cmd" id="ap-args">
        {current.tool}
        {"\n"}
        {formatArgs(current.args)}
      </pre>

      {current.tool === "ai.delegate" ? (
        // Egress gets its own preview: the §6 box, built from what was actually
        // rendered. The generic EFFECT block would bury the one number that matters.
        <EgressPreview preview={current.preview} />
      ) : current.tool === "ai.graph" ? (
        // A graph card carries a whole plan. The generic EFFECT block shows one line of
        // it, which is how somebody approves twelve tasks they never saw.
        <GraphCard preview={current.preview} />
      ) : current.tool === "pipe.run" ? (
        // A pipeline card authorises several actions at once - the only card in ORACLE
        // that does. Its own component rather than a GraphCard variant, because its
        // columns are tools and arguments where a graph's are roles and agents, and one
        // of those sets would always be a lie.
        <PipelineCard preview={current.preview} />
      ) : (
        (effect.summary || effect.detail) && (
          <div className="ap-effect">
            <span className="ap-effect-label">EFFECT</span>
            {effect.summary && <p className="ap-effect-summary">{effect.summary}</p>}
            {effect.detail && <pre className="ap-effect-detail">{effect.detail}</pre>}
          </div>
        )
      )}

      <p className="ap-rule">
        RULE <code>{current.rule}</code>
      </p>

      {current.tainted && (
        <p className="ap-taint" role="note">
          ⚠ This turn is <b>TAINTED</b> — it was built from content ORACLE did not author
          {current.escalated ? ", and the tier was raised because of it" : ""}.
        </p>
      )}

      <p className={`ap-expiry${expired ? " gone" : ""}`} role="timer" aria-live="off">
        {expired ? "expired — this approval can no longer be used" : `expires in ${remaining}s`}
      </p>

      {isT3 && !expired && (
        <label className="ap-phrase">
          Type <code>{requiredPhrase}</code> to enable Approve
          <input
            value={phrase}
            onChange={(e) => setPhrase(e.target.value)}
            aria-label={`Type ${requiredPhrase} to confirm`}
            spellCheck={false}
            autoComplete="off"
          />
        </label>
      )}

      <div className="ap-actions">
        <button
          ref={denyRef}
          className="deny"
          onClick={() => onRespond(current.approvalId, false)}
        >
          Deny <kbd>D</kbd>
        </button>
        <button
          className="approve"
          disabled={!canApprove}
          onClick={() => onRespond(current.approvalId, true)}
          title={
            expired
              ? "expired"
              : !cooldownOver
                ? "wait a moment"
                : !phraseOk
                  ? "type the confirmation phrase"
                  : undefined
          }
        >
          Approve {!isT3 && <kbd>A</kbd>}
        </button>
      </div>
    </section>
  );
}

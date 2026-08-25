/**
 * The Memory view — docs/MEMORY.md §6, and the non-negotiable half of having memory at
 * all: **"why does ORACLE think that?" in one click, and "make it stop thinking that" in
 * two.** A memory system without an undo button is a liability.
 *
 * Three things it insists on, each because leaving it out is a specific way to be wrong:
 *
 * - **Source is shown next to every value.** `user_stated` and `observed` are different
 *   claims about the world, and a list that renders them identically invites a person to
 *   trust an inference the way they trust their own instruction.
 * - **Beliefs ORACLE dropped are still here.** A superseded fact renders under the one
 *   that replaced it, greyed and labelled. "Why did it used to think that?" is a
 *   question people actually ask, and a store that answered it while the UI hid the
 *   answer would be keeping the row for nobody.
 * - **Stale is visible, not silent.** A fact unconfirmed for 90 days loses confidence
 *   (MEMORY.md §3). It is still shown — it may well be right — but it says so.
 */

import { asRecord, num, str } from "../protocol";

export interface MemoryFact {
  id: string;
  kind: string;
  scope: string;
  scopeRef: string;
  key: string;
  value: string;
  source: string;
  confidence: number;
  effectiveConfidence: number;
  stale: boolean;
  evidence: string[];
  origin: string;
  createdAt: string;
  lastConfirmedAt: string;
  hitCount: number;
  supersededBy: string;
}

/** What each source actually claims. Spelled out because the enum values are ORACLE's
 *  vocabulary, not a person's, and the difference between them is the whole point of
 *  showing them. */
const SOURCE_LABEL: Record<string, string> = {
  user_stated: "you told me",
  user_corrected: "you corrected me",
  observed: "I watched it work twice",
  inferred: "I inferred it (and you approved)",
};

export function toFacts(raw: unknown): MemoryFact[] {
  const rows = Array.isArray(asRecord(raw).facts) ? (asRecord(raw).facts as unknown[]) : [];
  return rows.map((entry) => {
    const r = asRecord(entry);
    return {
      id: str(r.id),
      kind: str(r.kind),
      scope: str(r.scope),
      scopeRef: str(r.scope_ref),
      key: str(r.key),
      value: str(r.value),
      source: str(r.source),
      confidence: num(r.confidence, 1),
      effectiveConfidence: num(r.effective_confidence, num(r.confidence, 1)),
      stale: r.stale === true,
      evidence: Array.isArray(r.evidence) ? r.evidence.map(String) : [],
      origin: str(r.origin),
      createdAt: str(r.created_at),
      lastConfirmedAt: str(r.last_confirmed_at),
      hitCount: num(r.hit_count),
      supersededBy: str(r.superseded_by),
    };
  });
}

export interface MemoryViewProps {
  facts: MemoryFact[];
  onForget(factId: string): void;
}

function days(since: string): string {
  const then = Date.parse(since);
  if (Number.isNaN(then)) return "unknown age";
  const d = Math.floor((Date.now() - then) / 86_400_000);
  return d <= 0 ? "today" : d === 1 ? "1 day ago" : `${d} days ago`;
}

function FactRow({
  fact,
  replaced,
  onForget,
}: {
  fact: MemoryFact;
  replaced: MemoryFact[];
  onForget(factId: string): void;
}) {
  return (
    <li className={`mem-fact mem-${fact.kind}${fact.stale ? " mem-stale" : ""}`}>
      <div className="mem-head">
        <span className="mem-key">{fact.key}</span>
        <span className="mem-value">{fact.value}</span>
        {fact.scopeRef && <span className="mem-scope">{fact.scopeRef}</span>}
        <span className="mem-source">{SOURCE_LABEL[fact.source] ?? fact.source}</span>
        <button type="button" onClick={() => onForget(fact.id)}>
          forget
        </button>
      </div>
      <div className="mem-meta">
        confirmed {days(fact.lastConfirmedAt)} · used {fact.hitCount}×
        {fact.stale && <span className="mem-stale-note"> · UNCONFIRMED FOR 90 DAYS</span>}
        {fact.effectiveConfidence !== fact.confidence && (
          <span className="mem-confidence"> · confidence {fact.effectiveConfidence}</span>
        )}
      </div>
      {/* One click. The whole reason the row carries `origin` and `evidence`. */}
      <details className="mem-why">
        <summary>why does ORACLE think that?</summary>
        <ul className="mem-why-list">
          <li>{SOURCE_LABEL[fact.source] ?? fact.source}</li>
          <li>first recorded {days(fact.createdAt)}</li>
          {fact.origin && <li>from {fact.origin}</li>}
          {fact.evidence.map((e) => (
            <li key={e}>
              evidence: <code>{e}</code>
            </li>
          ))}
        </ul>
        {replaced.length > 0 && (
          <ol className="mem-replaced">
            {replaced.map((old) => (
              <li key={old.id}>
                previously <b>{old.value}</b> ({SOURCE_LABEL[old.source] ?? old.source}, until{" "}
                {days(old.lastConfirmedAt)})
              </li>
            ))}
          </ol>
        )}
      </details>
    </li>
  );
}

export function MemoryView({ facts, onForget }: MemoryViewProps) {
  const live = facts.filter((f) => !f.supersededBy);
  const byReplacement = new Map<string, MemoryFact[]>();
  for (const fact of facts) {
    if (!fact.supersededBy) continue;
    byReplacement.set(fact.supersededBy, [...(byReplacement.get(fact.supersededBy) ?? []), fact]);
  }

  if (facts.length === 0) {
    return (
      <section className="memory" aria-label="Memory">
        <p className="muted">
          ORACLE has recorded nothing yet. It only remembers what you tell it, what you
          correct, and what it has watched work twice.
        </p>
      </section>
    );
  }

  return (
    <section className="memory" aria-label="Memory">
      <header className="mem-head-bar">
        <span>{live.length} remembered</span>
        {facts.length > live.length && (
          <span className="muted">{facts.length - live.length} replaced, kept</span>
        )}
      </header>
      <ul className="mem-facts">
        {live.map((fact) => (
          <FactRow
            key={fact.id}
            fact={fact}
            replaced={byReplacement.get(fact.id) ?? []}
            onForget={onForget}
          />
        ))}
      </ul>
    </section>
  );
}

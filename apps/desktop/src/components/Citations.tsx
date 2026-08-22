/**
 * Sources behind a retrieved answer — docs/RAG.md#7.
 *
 * Attribution is mandatory rather than decorative, for two reasons that pull in the same
 * direction:
 *
 *   1. **Trust.** An uncited claim from a 0.8b model is worthless. A cited one is
 *      checkable in one click, which is the difference between a tool you rely on and a
 *      tool you have to double-check by hand anyway.
 *   2. **Security.** `provenance` is what feeds taint tracking. A chunk somebody else
 *      wrote is `local_foreign`, and a turn built on one escalates the tier of whatever
 *      it plans next (docs/SECURITY.md#6). The badge here is the human-readable face of
 *      the machinery the gate is already running.
 *
 * So a citation with no path does not render as a citation. There is no "source
 * unavailable" state, because a source that cannot be shown cannot be checked, and a
 * citation that cannot be checked is worse than none — it looks like evidence.
 */

import { asRecord, num, str } from "../protocol";

export interface Citation {
  chunkId: string;
  project: string;
  path: string;
  absPath: string;
  anchor: string;
  score: number;
  provenance: string;
  indexedAt: string;
}

export interface CitationsProps {
  citations: Citation[];
  /** True when any source is `local_foreign` — the turn is tainted. */
  tainted?: boolean;
  /** Set when the embedding model was unavailable and only BM25 ran. */
  degraded?: boolean;
  onOpen(citation: Citation): void;
}

/** Parse the `know.*` tool payload. Anything without a path is dropped, not rendered. */
export function toCitations(results: unknown): Citation[] {
  if (!Array.isArray(results)) return [];
  return results
    .map((raw) => {
      const r = asRecord(raw);
      return {
        chunkId: str(r.chunk_id),
        project: str(r.project),
        path: str(r.path),
        absPath: str(r.abs_path),
        anchor: str(r.anchor),
        score: num(r.score),
        provenance: str(r.provenance, "local_owned"),
        indexedAt: str(r.indexed_at),
      };
    })
    .filter((c) => c.path !== "" && c.absPath !== "");
}

function Badge({ provenance }: { provenance: string }) {
  if (provenance === "local_owned") return null;
  // Icon plus text, never colour alone (docs/UI.md#1).
  return (
    <span className="cite-foreign" title="Written by someone else. This turn is tainted.">
      <span aria-hidden="true">⚠</span> {provenance.replace("local_", "")}
    </span>
  );
}

export function Citations({ citations, tainted, degraded, onOpen }: CitationsProps) {
  if (citations.length === 0) return null;

  return (
    <section className="citations" aria-label="Sources">
      <header className="cite-head">
        <span className="cite-title">
          {citations.length} {citations.length === 1 ? "source" : "sources"}
        </span>
        {tainted && (
          <span className="cite-taint" role="status">
            <span aria-hidden="true">⚠</span> built from untrusted content — actions will ask first
          </span>
        )}
        {degraded && (
          <span className="cite-degraded" role="status">
            <span aria-hidden="true">◑</span> keyword search only — embedding model unavailable
          </span>
        )}
      </header>

      <ol className="cite-list">
        {citations.map((c, i) => (
          <li key={c.chunkId || `${c.path}-${i}`} className="cite-item">
            <button
              className="cite-link"
              onClick={() => onOpen(c)}
              title={c.absPath}
              aria-label={`Open ${c.path}${c.anchor ? `, ${c.anchor}` : ""}`}
            >
              <span className="cite-index" aria-hidden="true">
                [{i + 1}]
              </span>
              <code className="cite-path">{c.path}</code>
              {c.anchor && c.anchor !== "(file)" && (
                <span className="cite-anchor">{c.anchor}</span>
              )}
            </button>
            <span className="cite-meta">
              {c.project && <span className="cite-project">{c.project}</span>}
              <Badge provenance={c.provenance} />
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

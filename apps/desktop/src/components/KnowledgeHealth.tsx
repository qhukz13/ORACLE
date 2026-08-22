/**
 * Index health — docs/RAG.md#9: what is indexed, when, how big, what failed.
 *
 * The panel exists because retrieval fails *quietly*. A stale index, a collection that
 * indexed zero documents, a vault root that was renamed — none of these produce an
 * error. They produce slightly worse answers, indefinitely, and the only symptom is a
 * feeling that ORACLE "doesn't know about that". So the states this renders are chosen
 * to be the ones that are otherwise invisible:
 *
 *   - **not built** — the first-run state, and a state, not a failure
 *   - **wrong model** — built by a different embedding model, so it is not stale, it is
 *     wrong: the vectors are in a different space and every score is meaningless
 *   - **a collection with zero documents** — almost always a moved root
 *   - **failures** — files that were reached and could not be parsed
 */

import { asRecord, num, str } from "../protocol";

export interface CollectionHealth {
  id: string;
  documents: number;
  lastIndexed: string;
  bytes: number;
}

export interface KnowledgeHealthData {
  built: boolean;
  stale?: boolean;
  error?: string;
  model: string;
  path: string;
  fileBytes: number;
  chunks: number;
  vectors: number;
  collections: CollectionHealth[];
  failures: { path: string; error: string }[];
}

export function toHealth(raw: unknown): KnowledgeHealthData {
  const r = asRecord(raw);
  const collections = Array.isArray(r.collections) ? r.collections : [];
  const failures = Array.isArray(r.failures) ? r.failures : [];
  return {
    built: r.built === true,
    stale: r.stale === true,
    error: str(r.error),
    model: str(r.model),
    path: str(r.path),
    fileBytes: num(r.file_bytes),
    chunks: num(r.chunks),
    vectors: num(r.vectors),
    collections: collections.map((c) => {
      const row = asRecord(c);
      return {
        id: str(row.collection_id),
        documents: num(row.documents),
        lastIndexed: str(row.last_indexed),
        bytes: num(row.bytes),
      };
    }),
    failures: failures.map((f) => {
      const row = asRecord(f);
      return { path: str(row.rel_path), error: str(row.parse_error) };
    }),
  };
}

function mb(bytes: number): string {
  return bytes >= 1e6 ? `${(bytes / 1e6).toFixed(0)} MB` : `${Math.max(1, Math.round(bytes / 1e3))} KB`;
}

/** Absolute, not "3 hours ago": a stale index is a fact about a clock, not a vibe. */
function when(iso: string): string {
  if (!iso) return "never";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export interface KnowledgeHealthProps {
  data: KnowledgeHealthData;
  reindexing?: boolean;
  onReindex(full: boolean): void;
}

export function KnowledgeHealth({ data, reindexing, onReindex }: KnowledgeHealthProps) {
  if (!data.built) {
    return (
      <section className="know-health" aria-label="Index health">
        <header className="kh-head">
          <span className="kh-title">Project knowledge</span>
        </header>
        <p className="kh-empty" role="status">
          {data.stale ? (
            <>
              <span aria-hidden="true">⚠</span> This index was built by a different embedding
              model, so its scores mean nothing. Rebuild it.
            </>
          ) : (
            <>
              <span aria-hidden="true">○</span> Nothing indexed yet.
            </>
          )}
        </p>
        {data.error && <pre className="kh-error">{data.error}</pre>}
        <button className="primary" disabled={reindexing} onClick={() => onReindex(true)}>
          {reindexing ? "Building…" : "Build index"}
        </button>
        {/* Measured, and said plainly: a surprise hour of CPU is how a feature gets
            switched off and never switched back on. */}
        <p className="kh-note">A full build re-embeds everything and takes about an hour.</p>
      </section>
    );
  }

  const empty = data.collections.filter((c) => c.documents === 0);

  return (
    <section className="know-health" aria-label="Index health">
      <header className="kh-head">
        <span className="kh-title">Project knowledge</span>
        <span className="spacer" />
        <span className="kh-model" title="Vector dimension is fixed at build time">
          {data.model}
        </span>
      </header>

      <dl className="kh-stats">
        <div>
          <dt>Chunks</dt>
          <dd>{data.chunks.toLocaleString()}</dd>
        </div>
        <div>
          <dt>Vectors</dt>
          <dd>{data.vectors.toLocaleString()}</dd>
        </div>
        <div>
          <dt>Size</dt>
          <dd>{mb(data.fileBytes)}</dd>
        </div>
      </dl>

      <table className="kh-collections">
        <thead>
          <tr>
            <th scope="col">Collection</th>
            <th scope="col">Documents</th>
            <th scope="col">Last indexed</th>
          </tr>
        </thead>
        <tbody>
          {data.collections.map((c) => (
            <tr key={c.id} className={c.documents === 0 ? "kh-zero" : undefined}>
              <th scope="row">{c.id}</th>
              <td>{c.documents.toLocaleString()}</td>
              <td>{when(c.lastIndexed)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {empty.length > 0 && (
        <p className="kh-warn" role="status">
          <span aria-hidden="true">⚠</span> {empty.map((c) => c.id).join(", ")} indexed nothing —
          check the roots in config/collections.yaml.
        </p>
      )}

      {data.failures.length > 0 && (
        <details className="kh-failures">
          <summary>
            {data.failures.length} {data.failures.length === 1 ? "file" : "files"} could not be
            parsed
          </summary>
          <ul>
            {data.failures.map((f) => (
              <li key={f.path}>
                <code>{f.path}</code> — {f.error}
              </li>
            ))}
          </ul>
        </details>
      )}

      <div className="kh-actions">
        <button className="ghost" disabled={reindexing} onClick={() => onReindex(false)}>
          {reindexing ? "Updating…" : "Update"}
        </button>
        <button className="ghost" disabled={reindexing} onClick={() => onReindex(true)}>
          Rebuild (~1 hour)
        </button>
      </div>
    </section>
  );
}

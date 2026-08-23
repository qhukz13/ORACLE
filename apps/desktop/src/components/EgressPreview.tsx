/**
 * The egress preview block inside an `ai.delegate` approval card — the concrete
 * meaning of "local-first" in a system that talks to a cloud API: not *never send*,
 * but *never send without seeing it* (docs/INTEGRATIONS.md §6).
 *
 * Everything rendered here comes off the `approval.requested` payload, which the
 * server built from the packet it actually rendered — files, token count, redactions,
 * destination. The UI computes none of it; if a number is missing here, the owner did
 * not see it, and the fix is to add it to the event.
 */

import { asRecord, num, str } from "../protocol";

export interface EgressPreviewProps {
  preview: Record<string, unknown>;
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.map(String) : [];
}

export function EgressPreview({ preview }: EgressPreviewProps) {
  const p = asRecord(preview);
  const files = strings(p["files"]);
  const redactions = strings(p["redactions"]);
  const tainted = strings(p["tainted_sources"]);
  const tools = strings(p["allowed_tools"]);
  const dropped = num(p["dropped_excerpts"], 0);

  return (
    <div className="egress" data-testid="egress-preview">
      <p className="egress-dest">
        ⚠ SENDING TO <b>{str(p["adapter"], "agent")}</b> ({str(p["destination"], "unknown host")})
      </p>
      <p className="egress-size">
        {files.length} file{files.length === 1 ? "" : "s"} · {num(p["tokens"], 0)} tokens
        {tools.length > 0 && <> · delegate may use: {tools.join(", ")}</>}
      </p>
      <ul className="egress-files">
        {files.map((f) => (
          <li key={f}>
            <code>{f}</code>
          </li>
        ))}
      </ul>
      <p className="egress-redactions">
        {redactions.length === 0
          ? "No redactions were needed."
          : `${redactions.length} redaction${redactions.length === 1 ? "" : "s"} applied:`}
      </p>
      {redactions.length > 0 && (
        <ul className="egress-redactions-list">
          {redactions.map((r, i) => (
            <li key={`${r}-${i}`}>
              <code>{r}</code>
            </li>
          ))}
        </ul>
      )}
      {dropped > 0 && (
        <p className="egress-dropped">
          {dropped} excerpt{dropped === 1 ? "" : "s"} dropped to fit the token budget.
        </p>
      )}
      {tainted.length > 0 && (
        <p className="egress-taint" role="note">
          ⚠ Includes content ORACLE did not author: {tainted.join(", ")}
        </p>
      )}
      <p className="egress-note muted">
        The full packet is on disk at <code>{str(p["packet_dir"])}</code>. Editing the selection
        is not supported yet — deny, adjust the task, and try again.
      </p>
    </div>
  );
}

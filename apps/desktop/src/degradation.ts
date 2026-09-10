/**
 * What is currently unavailable, in one list — [ARCHITECTURE §8], [UI.md §16](../../../docs/UI.md).
 *
 * Two events describe the same kind of fact and neither is a superset of the other:
 *
 * - `system.health` — the boot health phase (ROADMAP P13). Every subsystem, checked once, each
 *   failure carrying the capability it costs.
 * - `system.degraded` — a subsystem falling over *later*, while the daemon runs.
 *
 * Rendering them separately would put Ollama in two banners at once, which is the duplication
 * [ADR-0029](../../../docs/DECISIONS.md#adr-0029--the-orbital-view-is-cut) has just been through.
 * Merging them needs one rule and it is not obvious: **the live event wins.** Boot says what was
 * true at boot; `system.degraded` says what is true now, and a subsystem that came up healthy and
 * then died must not be reported as healthy because the boot report is longer.
 *
 * The other thing this fixes is smaller and was live in the app: the banner used to end every
 * degradation with *"Slash commands and the command palette still work"* — which is the *reasoning*
 * fallback, and is simply false when the knowledge index is what is missing. Each row now carries
 * its own `lost`, which is the entire reason the health phase produces that string.
 */

export interface Degradation {
  component: string;
  /** What is wrong, in the subsystem's own words. */
  reason: string;
  /** What stops working because of it. Empty only for a legacy `system.degraded` payload. */
  lost: string;
  remedy: string;
}

/** The boot health phase's wire shape (docs/API.md `system.health`). */
export interface BootHealthWire {
  complete: boolean;
  ok: boolean;
  elapsedMs: number;
  probes: {
    component: string;
    ok: boolean;
    detail: string;
    lost: string;
    remedy: string;
    unknown: boolean;
  }[];
}

/**
 * Everything currently unavailable, newest information first.
 *
 * `live` is `system.degraded`'s single slot; it overrides a boot probe for the same component
 * rather than appending to it. An empty result means nothing is known to be missing — which,
 * when `health.complete` is false, means *nothing has been checked*, and the caller has to say
 * so rather than implying a clean bill of health.
 */
export function degradations(
  health: BootHealthWire | null,
  live: { component: string; reason: string; remedy: string } | null,
): Degradation[] {
  const out: Degradation[] = [];
  if (live) {
    out.push({
      component: live.component,
      reason: live.reason,
      // A `system.degraded` payload has no `lost` field — it predates the health phase. Left
      // empty rather than guessed at: inventing "and everything else still works" is how the
      // hardcoded banner sentence became wrong in the first place.
      lost: "",
      remedy: live.remedy,
    });
  }
  for (const probe of health?.probes ?? []) {
    if (probe.ok && !probe.unknown) continue;
    if (out.some((d) => d.component === probe.component)) continue; // the live event won
    out.push({
      component: probe.component,
      reason: probe.detail,
      lost: probe.lost,
      remedy: probe.remedy,
    });
  }
  return out;
}

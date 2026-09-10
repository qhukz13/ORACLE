/**
 * Merging the two sources of "something is missing".
 *
 * The rule that needed a test is precedence. `system.health` is a boot snapshot and
 * `system.degraded` is live, so a subsystem that came up fine and then died must not read as
 * fine because the boot report is longer and arrived first. Getting this backwards produces a
 * banner that is confidently wrong about the current state, which is worse than no banner.
 */

import { describe, expect, it } from "vitest";

import { degradations } from "./degradation";
import type { BootHealthWire } from "./degradation";

function probe(component: string, ok: boolean, extra: Partial<BootHealthWire["probes"][number]> = {}) {
  return {
    component,
    ok,
    detail: ok ? "fine" : "not reachable",
    lost: ok ? "" : `${component} — the fallback still works`,
    remedy: ok ? "" : `restart ${component}`,
    unknown: false,
    ...extra,
  };
}

function health(probes: BootHealthWire["probes"], complete = true): BootHealthWire {
  return { complete, ok: probes.every((p) => p.ok), elapsedMs: 12, probes };
}

describe("degradations()", () => {
  it("says nothing when everything came up", () => {
    expect(degradations(health([probe("reasoning", true)]), null)).toEqual([]);
  });

  it("reports a failed probe with the capability it costs", () => {
    /* The whole reason `lost` exists. A row that names the component and drops this tells the
       reader that something is broken without telling them whether to keep working. */
    const [row] = degradations(health([probe("knowledge", false)]), null);
    expect(row!.component).toBe("knowledge");
    expect(row!.lost).toContain("the fallback still works");
    expect(row!.remedy).toBe("restart knowledge");
  });

  it("lets the live event win over the boot snapshot for the same component", () => {
    /* The precedence rule. Ollama was up at boot and fell over an hour later; the banner must
       say it is down, not repeat that it was fine. */
    const rows = degradations(
      health([probe("reasoning", true), probe("knowledge", false)]),
      { component: "reasoning", reason: "connection refused", remedy: "start ollama" },
    );
    expect(rows.map((r) => r.component)).toEqual(["reasoning", "knowledge"]);
    expect(rows[0]!.reason).toBe("connection refused");
  });

  it("does not list one component twice when both sources name it", () => {
    /* The duplication OQ-14 cost the orbital view, in miniature: two renderings of one fact. */
    const rows = degradations(health([probe("reasoning", false)]), {
      component: "reasoning",
      reason: "connection refused",
      remedy: "start ollama",
    });
    expect(rows).toHaveLength(1);
    expect(rows[0]!.reason).toBe("connection refused");
  });

  it("leaves `lost` empty for a legacy payload rather than inventing one", () => {
    /* `system.degraded` predates the health phase and carries no `lost`. Filling in a plausible
       sentence is exactly how the old banner ended up telling people the palette still worked
       when the thing that was down had nothing to do with the palette. */
    const [row] = degradations(null, {
      component: "reasoning",
      reason: "connection refused",
      remedy: "start ollama",
    });
    expect(row!.lost).toBe("");
  });

  it("surfaces a probe that could not be checked, not just one that failed", () => {
    /* `unknown` means the probe timed out. A hung dependency is as actionable as a dead one,
       and filtering on `ok` alone would hide it — `ok` is false there, but a future refactor
       that treats unknown as "not a failure" must still not drop the row. */
    const rows = degradations(health([probe("delegation", false, { unknown: true })]), null);
    expect(rows).toHaveLength(1);
    expect(rows[0]!.component).toBe("delegation");
  });

  it("reports nothing when the phase has not run — which the caller must not read as healthy", () => {
    /* The list is empty both when everything is fine and when nothing has been checked. That is
       correct for this function and dangerous for its caller, so it is pinned here: `complete`
       is the field that tells them apart, and it lives on the payload, not on this result. */
    const unrun = health([], false);
    expect(degradations(unrun, null)).toEqual([]);
    expect(unrun.complete).toBe(false);
  });
});

/**
 * UI.md §14's contrast rule, verified rather than assumed.
 *
 * > *"Contrast: all text ≥ 4.5:1, all status indicators ≥ 3:1 against their surface. **Amber on
 * > dark is the risky one and must be verified, not assumed.**"*
 *
 * It had not been. `a11y.test.tsx` disables axe's `color-contrast` rule — correctly, because
 * happy-dom lays nothing out and a contrast answer from an engine with no geometry is a guess
 * wearing a number. So the one rule §14 singles out as risky was the one rule the audit could not
 * check, and nothing else checked it either.
 *
 * This does not need a DOM. Contrast is a pure function of two colours, and the colours are literals
 * in `styles.css`. So: parse the tokens out of the stylesheet itself — not a copy of them, which
 * would drift the first time somebody tunes a hue — and do the arithmetic.
 *
 * WCAG 2.1 relative luminance and contrast ratio, from the spec rather than from memory:
 *   L  = 0.2126·R + 0.7152·G + 0.0722·B, each channel linearised
 *   CR = (L_lighter + 0.05) / (L_darker + 0.05)
 */

import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

const CSS = readFileSync(resolve(__dirname, "styles.css"), "utf8");

/** Every `--name: #rrggbb;` in `:root`. Read from the stylesheet so the test cannot drift from it. */
function tokens(): Record<string, string> {
  const root = CSS.slice(CSS.indexOf(":root"), CSS.indexOf("}", CSS.indexOf(":root")));
  const out: Record<string, string> = {};
  for (const m of root.matchAll(/(--[\w-]+)\s*:\s*(#[0-9a-fA-F]{3,8})\s*;/g)) {
    const [, name, value] = m;
    if (name && value) out[name] = value;
  }
  return out;
}

function channel(v: number): number {
  const s = v / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

function luminance(hex: string): number {
  let h = hex.replace("#", "");
  if (h.length === 3) h = [...h].map((c) => c + c).join("");
  const rgb = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  if (rgb.length !== 3 || rgb.some(Number.isNaN)) {
    throw new Error(`${hex} is not a hex colour this test can read`);
  }
  const [r, g, b] = rgb as [number, number, number];
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrast(fg: string, bg: string): number {
  const lighter = Math.max(luminance(fg), luminance(bg));
  const darker = Math.min(luminance(fg), luminance(bg));
  return (lighter + 0.05) / (darker + 0.05);
}

const T = tokens();

/** A token by name, or a loud failure.
 *
 * `T[name]` is `string | undefined` under `strict`, and the tempting fix is a non-null assertion.
 * That would make a *renamed* token silently compare `undefined` against a surface and produce
 * `NaN`, which `toBeGreaterThanOrEqual` fails with a baffling message. Throwing names the token. */
function tok(name: string): string {
  const value = T[name];
  if (!value) throw new Error(`${name} is not defined in styles.css :root`);
  return value;
}

/** The surfaces §14 names: app → panel → raised → input. Status sits on all of them. */
const SURFACES = ["--bg-0", "--bg-1", "--bg-2", "--bg-3"] as const;
const STATUS = [
  "--st-idle",
  "--st-think",
  "--st-run",
  "--st-wait",
  "--st-ok",
  "--st-err",
  "--st-halt",
  "--st-ext",
] as const;

describe("the tokens are actually in the stylesheet", () => {
  it("parses every surface, text and status token §14 names", () => {
    // If this fails, the test is reading the wrong thing and every number below is meaningless.
    for (const name of [...SURFACES, ...STATUS, "--fg-0", "--fg-1", "--fg-2"]) {
      expect(T[name], `${name} missing from :root`).toBeTruthy();
    }
  });
});

describe("UI.md §14: all text ≥ 4.5:1", () => {
  // `--fg-2` is muted *decoration* — separators, timestamps, the dimmed half of a pair. §14's rule
  // is about text, and axe's own exemption for incidental text is the same distinction. It is
  // checked at the 3:1 non-text bar below rather than exempted silently.
  for (const fg of ["--fg-0", "--fg-1"] as const) {
    for (const bg of SURFACES) {
      it(`${fg} on ${bg}`, () => {
        expect(contrast(tok(fg), tok(bg))).toBeGreaterThanOrEqual(4.5);
      });
    }
  }
});

describe("UI.md §14: all status indicators ≥ 3:1", () => {
  for (const st of STATUS) {
    for (const bg of SURFACES) {
      it(`${st} on ${bg}`, () => {
        expect(contrast(tok(st), tok(bg))).toBeGreaterThanOrEqual(3);
      });
    }
  }
});

describe("the one §14 calls risky", () => {
  it("amber — --st-wait — clears 3:1 on every surface", () => {
    // Singled out because it is the token that means "needs a human". A status nobody can see is
    // a request nobody answers, and this is the colour the design flagged as most likely to fail.
    for (const bg of SURFACES) {
      const ratio = contrast(tok("--st-wait"), tok(bg));
      expect(ratio, `--st-wait on ${bg} is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(3);
    }
  });

  it("and is legible as text too, since approvals render words in it", () => {
    // `.pc-badge`, `.egress-dest` and the approval tier label are amber *text*, not just a dot,
    // so for those uses the 4.5:1 text bar is the one that applies.
    const ratio = contrast(tok("--st-wait"), tok("--bg-2"));
    expect(ratio, `amber text on a raised surface is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(
      4.5,
    );
  });
});

describe("--fg-2 is decoration, and is held to the non-text bar", () => {
  for (const bg of SURFACES) {
    it(`--fg-2 on ${bg} clears 3:1`, () => {
      expect(contrast(tok("--fg-2"), tok(bg))).toBeGreaterThanOrEqual(3);
    });
  }
});

/**
 * Citations are a trust surface, so these tests are about what must never be shown:
 * a source that cannot be opened, and a foreign source that looks like an owned one.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type Citation, Citations, toCitations } from "./Citations";

function citation(over: Partial<Citation> = {}): Citation {
  return {
    chunkId: "abc123",
    project: "Asterim",
    path: "apps/server/src/services/TokenService.ts",
    absPath: "C:/Projects/Asterim/apps/server/src/services/TokenService.ts",
    anchor: "signAccessToken",
    score: 0.83,
    provenance: "local_owned",
    indexedAt: "2026-08-22T09:02:11Z",
    ...over,
  };
}

describe("toCitations", () => {
  it("maps the know.* payload", () => {
    const out = toCitations([
      {
        chunk_id: "c1",
        project: "Asterim",
        path: "a.ts",
        abs_path: "C:/Projects/Asterim/a.ts",
        anchor: "TokenService",
        score: 0.5,
        provenance: "local_owned",
        indexed_at: "2026-08-22T00:00:00Z",
      },
    ]);
    expect(out).toHaveLength(1);
    expect(out[0]!.path).toBe("a.ts");
    expect(out[0]!.anchor).toBe("TokenService");
  });

  it("drops anything without a path", () => {
    // A citation that cannot be opened cannot be checked, and an uncheckable citation
    // is worse than none — it still reads as evidence.
    const out = toCitations([{ chunk_id: "c1", project: "Asterim", score: 0.9 }]);
    expect(out).toEqual([]);
  });

  it("survives a non-array payload", () => {
    expect(toCitations(undefined)).toEqual([]);
    expect(toCitations({ nope: true })).toEqual([]);
  });

  it("defaults missing provenance to owned rather than dropping the source", () => {
    const out = toCitations([{ chunk_id: "c", path: "a.ts", abs_path: "C:/a.ts" }]);
    expect(out[0]!.provenance).toBe("local_owned");
  });
});

describe("Citations", () => {
  it("renders nothing when there are no sources", () => {
    const { container } = render(<Citations citations={[]} onOpen={vi.fn()} />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the path and the symbol, and opens on click", async () => {
    const onOpen = vi.fn();
    render(<Citations citations={[citation()]} onOpen={onOpen} />);

    const link = screen.getByRole("button", {
      name: /apps\/server\/src\/services\/TokenService\.ts/,
    });
    expect(link).toBeTruthy();
    expect(screen.getByText("signAccessToken")).toBeTruthy();

    link.click();
    expect(onOpen).toHaveBeenCalledWith(expect.objectContaining({ path: expect.any(String) }));
  });

  it("marks foreign content and does not mark owned content", () => {
    render(
      <Citations
        citations={[citation({ provenance: "local_foreign", chunkId: "x" })]}
        tainted
        onOpen={vi.fn()}
      />,
    );
    expect(screen.getByText(/foreign/)).toBeTruthy();
    expect(screen.getByRole("status").textContent).toMatch(/untrusted content/i);
  });

  it("says nothing about taint when every source is owned", () => {
    render(<Citations citations={[citation()]} onOpen={vi.fn()} />);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByText(/foreign/)).toBeNull();
  });

  it("hides the placeholder anchor rather than showing '(file)'", () => {
    // "(file)" is the chunker's marker for a file preamble. It is not a symbol name and
    // means nothing to a reader.
    render(<Citations citations={[citation({ anchor: "(file)" })]} onOpen={vi.fn()} />);
    expect(screen.queryByText("(file)")).toBeNull();
  });

  it("announces the degraded search mode", () => {
    render(<Citations citations={[citation()]} degraded onOpen={vi.fn()} />);
    expect(screen.getByRole("status").textContent).toMatch(/keyword search only/i);
  });

  it("labels the list for screen readers", () => {
    render(<Citations citations={[citation()]} onOpen={vi.fn()} />);
    expect(screen.getByRole("region", { name: "Sources" })).toBeTruthy();
  });
});

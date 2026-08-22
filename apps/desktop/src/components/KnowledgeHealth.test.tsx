/**
 * The panel exists for states that are otherwise invisible, so the tests are about those
 * states rather than about the happy path looking tidy.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KnowledgeHealth, type KnowledgeHealthData, toHealth } from "./KnowledgeHealth";

function health(over: Partial<KnowledgeHealthData> = {}): KnowledgeHealthData {
  return {
    built: true,
    model: "multilingual-e5-base",
    path: "D:/ORACLE/data/knowledge.db",
    fileBytes: 82_000_000,
    chunks: 10_269,
    vectors: 9_368,
    collections: [
      { id: "projects", documents: 1170, lastIndexed: "2026-08-22T00:00:00Z", bytes: 40_000_000 },
      { id: "notes", documents: 160, lastIndexed: "2026-08-22T00:00:00Z", bytes: 2_000_000 },
    ],
    failures: [],
    ...over,
  };
}

describe("toHealth", () => {
  it("maps the API payload", () => {
    const data = toHealth({
      built: true,
      model: "e5",
      chunks: 5,
      vectors: 4,
      file_bytes: 100,
      collections: [{ collection_id: "projects", documents: 2, last_indexed: "x", bytes: 9 }],
      failures: [{ rel_path: "a.pdf", parse_error: "no text layer" }],
    });
    expect(data.built).toBe(true);
    expect(data.collections[0]!.id).toBe("projects");
    expect(data.failures[0]!.path).toBe("a.pdf");
  });

  it("survives a payload with nothing in it", () => {
    const data = toHealth(undefined);
    expect(data.built).toBe(false);
    expect(data.collections).toEqual([]);
    expect(data.failures).toEqual([]);
  });
});

describe("KnowledgeHealth", () => {
  it("treats an unbuilt index as a state, not an error", () => {
    render(
      <KnowledgeHealth data={health({ built: false })} onReindex={vi.fn()} />,
    );
    expect(screen.getByText(/Nothing indexed yet/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /Build index/ })).toBeTruthy();
  });

  it("warns before spending an hour of CPU", () => {
    // A surprise hour of full-tilt CPU is how a feature gets switched off for good.
    render(<KnowledgeHealth data={health({ built: false })} onReindex={vi.fn()} />);
    expect(screen.getByText(/about an hour/)).toBeTruthy();
  });

  it("says an index built by another model is wrong, not merely old", () => {
    render(
      <KnowledgeHealth
        data={health({ built: false, stale: true, error: "delete it and reindex" })}
        onReindex={vi.fn()}
      />,
    );
    expect(screen.getByRole("status").textContent).toMatch(/different embedding model/i);
  });

  it("flags a collection that indexed nothing", () => {
    // Almost always a moved or renamed root, and otherwise completely silent.
    render(
      <KnowledgeHealth
        data={health({
          collections: [
            { id: "projects", documents: 1170, lastIndexed: "2026-08-22T00:00:00Z", bytes: 1 },
            { id: "notes", documents: 0, lastIndexed: "", bytes: 0 },
          ],
        })}
        onReindex={vi.fn()}
      />,
    );
    expect(screen.getByRole("status").textContent).toMatch(/notes indexed nothing/);
  });

  it("says nothing when every collection has documents", () => {
    render(<KnowledgeHealth data={health()} onReindex={vi.fn()} />);
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("lists parse failures rather than hiding them", () => {
    render(
      <KnowledgeHealth
        data={health({ failures: [{ path: "book.pdf", error: "no text layer" }] })}
        onReindex={vi.fn()}
      />,
    );
    expect(screen.getByText(/1 file could not be parsed/)).toBeTruthy();
    expect(screen.getByText("book.pdf")).toBeTruthy();
  });

  it("shows counts and size", () => {
    render(<KnowledgeHealth data={health()} onReindex={vi.fn()} />);
    expect(screen.getByText("10,269")).toBeTruthy();
    expect(screen.getByText("82 MB")).toBeTruthy();
  });

  it("distinguishes an update from a rebuild", () => {
    const onReindex = vi.fn();
    render(<KnowledgeHealth data={health()} onReindex={onReindex} />);
    screen.getByRole("button", { name: "Update" }).click();
    expect(onReindex).toHaveBeenCalledWith(false);
    screen.getByRole("button", { name: /Rebuild/ }).click();
    expect(onReindex).toHaveBeenCalledWith(true);
  });

  it("disables both actions while indexing", () => {
    render(<KnowledgeHealth data={health()} reindexing onReindex={vi.fn()} />);
    for (const button of screen.getAllByRole("button")) {
      expect((button as HTMLButtonElement).disabled).toBe(true);
    }
  });
});

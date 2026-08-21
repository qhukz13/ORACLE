/**
 * The Confirmation Center is the one UI surface that is part of the security model,
 * so these tests are about what it makes HARD, not about what it renders.
 *
 * Each one corresponds to a rule in docs/UI.md#9 that exists because of a specific way
 * a confirmation dialog becomes a rubber stamp.
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Approval } from "../protocol";
import { ConfirmationCenter } from "./ConfirmationCenter";

function approval(over: Partial<Approval> = {}): Approval {
  return {
    approvalId: "ap_1",
    tool: "git.push",
    tier: "T2",
    decision: "confirm",
    rule: "tools.git.push.tier",
    tainted: false,
    escalated: false,
    args: { path: "C:\\Projects\\Asterim", remote: "origin", branch: "fix/auth" },
    preview: { summary: "3 commits would be published to origin/fix/auth" },
    expiresInSec: 180,
    issuedAt: Date.now(),
    ...over,
  };
}

describe("the card shows the real action", () => {
  it("renders the arguments verbatim, not a paraphrase", () => {
    const { container } = render(
      <ConfirmationCenter approvals={[approval()]} decided={[]} onRespond={vi.fn()} />,
    );
    // The command block specifically. `git.push` also appears inside the rule name, and
    // what this test is about is the text the user is being asked to approve.
    const block = container.querySelector("#ap-args") as HTMLElement;
    expect(block.textContent).toContain("git.push");
    expect(block.textContent).toContain("C:\\Projects\\Asterim");
    expect(block.textContent).toContain("remote: origin");
    expect(block.textContent).toContain("branch: fix/auth");
  });

  it("names the rule that fired", () => {
    render(<ConfirmationCenter approvals={[approval()]} decided={[]} onRespond={vi.fn()} />);
    expect(screen.getByText("tools.git.push.tier")).toBeTruthy();
  });

  it("shows the effect from the preview", () => {
    render(<ConfirmationCenter approvals={[approval()]} decided={[]} onRespond={vi.fn()} />);
    expect(screen.getByText(/3 commits would be published/)).toBeTruthy();
  });

  it("shows the taint warning when the turn is tainted", () => {
    render(
      <ConfirmationCenter
        approvals={[approval({ tainted: true, escalated: true })]}
        decided={[]}
        onRespond={vi.fn()}
      />,
    );
    const note = screen.getByRole("note");
    expect(note.textContent).toContain("TAINTED");
    expect(note.textContent).toContain("tier was raised");
  });

  it("says nothing about taint when the turn is clean", () => {
    render(<ConfirmationCenter approvals={[approval()]} decided={[]} onRespond={vi.fn()} />);
    expect(screen.queryByRole("note")).toBeNull();
  });
});

describe("approving is deliberately harder than denying", () => {
  it("focuses Deny, never Approve", () => {
    render(<ConfirmationCenter approvals={[approval()]} decided={[]} onRespond={vi.fn()} />);
    expect(document.activeElement?.textContent).toContain("Deny");
  });

  it("blocks approval during the guard window, then allows it", async () => {
    const onRespond = vi.fn();
    render(<ConfirmationCenter approvals={[approval()]} decided={[]} onRespond={onRespond} />);

    const approve = screen.getByRole("button", { name: /Approve/ }) as HTMLButtonElement;
    // An Enter carried over from the previous action must not land here.
    expect(approve.disabled).toBe(true);

    await waitFor(() => expect(approve.disabled).toBe(false), { timeout: 2000 });
    fireEvent.click(approve);
    expect(onRespond).toHaveBeenCalledWith("ap_1", true);
  });

  it("denies immediately — the safe action is never delayed", () => {
    const onRespond = vi.fn();
    render(<ConfirmationCenter approvals={[approval()]} decided={[]} onRespond={onRespond} />);
    fireEvent.click(screen.getByRole("button", { name: /Deny/ }));
    expect(onRespond).toHaveBeenCalledWith("ap_1", false);
  });

  it("Escape denies", () => {
    const onRespond = vi.fn();
    render(<ConfirmationCenter approvals={[approval()]} decided={[]} onRespond={onRespond} />);
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onRespond).toHaveBeenCalledWith("ap_1", false);
  });
});

describe("T3 costs a moment's attention", () => {
  const t3 = () =>
    approval({ tool: "fs.delete", tier: "T3", decision: "confirm_strong", args: { path: "C:\\x" } });

  it("requires the confirmation phrase to be typed", async () => {
    const onRespond = vi.fn();
    render(<ConfirmationCenter approvals={[t3()]} decided={[]} onRespond={onRespond} />);

    const approve = screen.getByRole("button", { name: /Approve/ }) as HTMLButtonElement;
    await new Promise((r) => setTimeout(r, 3100));
    // Cooldown is over, but the phrase is still missing.
    expect(approve.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText(/Type fs\.delete to confirm/), {
      target: { value: "fs.delete" },
    });
    await waitFor(() => expect(approve.disabled).toBe(false));
  });

  it("ignores the A shortcut, so a destructive action needs the mouse or Tab", async () => {
    const onRespond = vi.fn();
    render(<ConfirmationCenter approvals={[t3()]} decided={[]} onRespond={onRespond} />);
    await new Promise((r) => setTimeout(r, 3100));
    fireEvent.keyDown(window, { key: "a" });
    expect(onRespond).not.toHaveBeenCalled();
  });
});

describe("expiry is real", () => {
  it("disables Approve and says so once expired", async () => {
    render(
      <ConfirmationCenter
        approvals={[approval({ expiresInSec: 0 })]}
        decided={[]}
        onRespond={vi.fn()}
      />,
    );
    await waitFor(() =>
      expect(screen.getByRole("timer").textContent).toContain("can no longer be used"),
    );
    const approve = screen.getByRole("button", { name: /Approve/ }) as HTMLButtonElement;
    expect(approve.disabled).toBe(true);
  });
});

describe("expiry uses the server's clock, not ours", () => {
  it("shows a replayed approval as already expired", async () => {
    // After a reload the client resumes from seq 0, so an approval issued by a backend
    // that has since exited arrives looking brand new. Counting from receipt would show
    // a live countdown for a request nothing can answer.
    const stale = approval({ expiresInSec: 180, issuedAt: Date.now() - 10 * 60 * 1000 });
    render(<ConfirmationCenter approvals={[stale]} decided={[]} onRespond={vi.fn()} />);

    await waitFor(() =>
      expect(screen.getByRole("timer").textContent).toContain("can no longer be used"),
    );
    expect((screen.getByRole("button", { name: /Approve/ }) as HTMLButtonElement).disabled).toBe(
      true,
    );
  });
});

describe("the effect is a real preview, not a description", () => {
  it("renders the dry run's own output verbatim", () => {
    const withDetail = approval({
      preview: {
        summary: "Publishes commits to a remote. Cannot be taken back.",
        detail: ["2 commit(s) would be published to origin/main", "abc1234 fix the thing"].join(
          "\n",
        ),
      },
    });
    const { container } = render(
      <ConfirmationCenter approvals={[withDetail]} decided={[]} onRespond={vi.fn()} />,
    );
    const detail = container.querySelector(".ap-effect-detail") as HTMLElement;
    expect(detail.textContent).toContain("2 commit(s) would be published");
    expect(detail.textContent).toContain("abc1234 fix the thing");
  });

  it("renders a list-shaped preview one entry per line", () => {
    const withList = approval({
      tool: "fs.delete",
      preview: { summary: "Moves the target into the trash.", detail: ["a.txt", "b.txt"] },
    });
    const { container } = render(
      <ConfirmationCenter approvals={[withList]} decided={[]} onRespond={vi.fn()} />,
    );
    expect((container.querySelector(".ap-effect-detail") as HTMLElement).textContent).toBe(
      ["a.txt", "b.txt"].join("\n"),
    );
  });
});

describe("a queue is decided one at a time", () => {
  it("shows only the first card and counts the rest", () => {
    render(
      <ConfirmationCenter
        approvals={[approval(), approval({ approvalId: "ap_2", tool: "fs.delete" })]}
        decided={[]}
        onRespond={vi.fn()}
      />,
    );
    expect(screen.getByText("+1 queued")).toBeTruthy();
    // No "approve all" affordance exists, by design.
    expect(screen.queryByText(/approve all/i)).toBeNull();
    expect(screen.getAllByRole("button", { name: /Approve/ })).toHaveLength(1);
  });
});

describe("when nothing is pending", () => {
  beforeEach(() => {
    document.body.innerHTML = "";
  });

  it("renders nothing at all", () => {
    const { container } = render(
      <ConfirmationCenter approvals={[]} decided={[]} onRespond={vi.fn()} />,
    );
    expect(container.innerHTML).toBe("");
  });

  it("shows the last outcome once one has been decided", () => {
    render(
      <ConfirmationCenter
        approvals={[]}
        decided={[{ ...approval(), resolution: "refused" }]}
        onRespond={vi.fn()}
      />,
    );
    expect(screen.getByText(/git\.push — refused/)).toBeTruthy();
  });
});

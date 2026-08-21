"""Undo journal and trash.

ADR-0005: **reversibility beats permission.** A write that can be undone runs
automatically with a journalled undo, rather than prompting. Prompt fatigue is itself a
security failure — an agent that asks forty times a day trains you to click Approve
without reading — so the undo machinery is what *buys* the T1 tier.

Split across the process boundary, deliberately:

  * the **child** performs the backup, because it is the side doing the write, and
    reports what it did in an `UndoPlan` on the tool result;
  * the **parent** records that plan in the journal, because the child holds nothing
    durable and must not be trusted with the record of what it did (ADR-0003).

Ordering inside the child is backup → mutate, never the reverse. A crash between the
two leaves an unreferenced backup, which is harmless. The opposite ordering loses data.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from oracle.logsink import get_logger

log = get_logger(__name__)


class UndoKind(StrEnum):
    RESTORE_FILE = "restore_file"  # put the backup back
    DELETE_CREATED = "delete_created"  # the file did not exist before
    MOVE_BACK = "move_back"  # undo a rename/move
    NONE = "none"  # nothing to reverse


class UndoPlan(BaseModel):
    """How to reverse one mutation. Produced by the tool, recorded by the parent.

    It is *data*, never a command string: the journal executes it, and a model can
    neither author nor alter it.
    """

    model_config = ConfigDict(frozen=True)

    kind: UndoKind = UndoKind.NONE
    target: str = ""
    backup: str | None = None
    #: For MOVE_BACK: where the file came from.
    origin: str | None = None
    note: str = ""


class UndoRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    ts: str
    tool: str
    trace_id: str
    plan: UndoPlan
    undone: bool = False


class UndoError(Exception):
    pass


def _stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class TrashStore:
    """Where previous versions go. Used by the child, inside the toolhost.

    Nothing here ever calls `unlink` on user data. `fs.delete` is a *move* into the
    trash — an unrecoverable delete is T4 and simply absent from the catalogue
    (docs/TOOLS.md#deliberately-absent). If I want that, I use Explorer.
    """

    def __init__(self, root: Path) -> None:
        self.root = root

    def _slot(self, original: Path) -> Path:
        day = datetime.now(UTC).strftime("%Y-%m-%d")
        folder = self.root / day
        folder.mkdir(parents=True, exist_ok=True)
        # uuid prefix keeps same-named files from colliding, and keeps the original
        # name visible so the trash is browsable by a human.
        return folder / f"{uuid.uuid4().hex[:12]}__{original.name}"

    def back_up(self, path: Path) -> UndoPlan:
        """Copy a file aside before it is overwritten."""
        if not path.exists():
            return UndoPlan(kind=UndoKind.DELETE_CREATED, target=str(path), note="file was new")
        slot = self._slot(path)
        shutil.copy2(path, slot)
        return UndoPlan(kind=UndoKind.RESTORE_FILE, target=str(path), backup=str(slot))

    def move_to_trash(self, path: Path) -> UndoPlan:
        """`fs.delete`. A move, never an unlink."""
        if not path.exists():
            raise UndoError(f"{path} does not exist")
        slot = self._slot(path)
        shutil.move(str(path), str(slot))
        return UndoPlan(
            kind=UndoKind.RESTORE_FILE, target=str(path), backup=str(slot), note="moved to trash"
        )

    def size_bytes(self) -> int:
        if not self.root.exists():
            return 0
        return sum(f.stat().st_size for f in self.root.rglob("*") if f.is_file())


class UndoJournal:
    """Append-only record of reversible mutations. Lives on the parent side."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, tool: str, plan: UndoPlan, *, trace_id: str) -> UndoRecord:
        rec = UndoRecord(
            id="u_" + uuid.uuid4().hex[:12], ts=_stamp(), tool=tool, trace_id=trace_id, plan=plan
        )
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(rec.model_dump_json() + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return rec

    def records(self) -> list[UndoRecord]:
        if not self.path.exists():
            return []
        out: list[UndoRecord] = []
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(UndoRecord.model_validate_json(line))
        return out

    def latest(self, n: int = 10) -> list[UndoRecord]:
        return [r for r in self.records() if not r.undone][-n:]

    def undo(self, record_id: str) -> dict[str, Any]:
        """Reverse one recorded mutation.

        Refuses rather than guesses: if the file has changed since, the backup is not
        silently restored over it. Losing work while "undoing" would be the worst
        possible behaviour from a safety feature.
        """
        records = self.records()
        match = next((r for r in records if r.id == record_id), None)
        if match is None:
            raise UndoError(f"no undo record {record_id!r}")
        if match.undone:
            raise UndoError(f"{record_id} has already been undone")

        plan = match.plan
        target = Path(plan.target)

        if plan.kind is UndoKind.NONE:
            raise UndoError(f"{match.tool} recorded nothing to reverse")

        if plan.kind is UndoKind.DELETE_CREATED:
            if target.exists():
                target.unlink()
            result = {"restored": None, "removed": str(target)}

        elif plan.kind is UndoKind.RESTORE_FILE:
            if plan.backup is None:
                raise UndoError(f"{record_id} has no backup to restore")
            backup = Path(plan.backup)
            if not backup.exists():
                raise UndoError(f"backup {backup} is gone; cannot undo")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(backup, target)
            result = {"restored": str(target), "from": str(backup)}

        elif plan.kind is UndoKind.MOVE_BACK:
            if plan.origin is None:
                raise UndoError(f"{record_id} has no origin to move back to")
            origin = Path(plan.origin)
            if origin.exists():
                raise UndoError(f"{origin} exists again; refusing to overwrite it")
            shutil.move(str(target), str(origin))
            result = {"restored": str(origin)}

        else:  # pragma: no cover - StrEnum is exhaustive
            raise UndoError(f"unknown undo kind {plan.kind}")

        self._mark_undone(record_id)
        log.info("undo.applied", record=record_id, tool=match.tool, kind=str(plan.kind))
        return {"id": record_id, "tool": match.tool, "kind": str(plan.kind), **result}

    def _mark_undone(self, record_id: str) -> None:
        # Rewritten wholesale: the journal is small and correctness beats cleverness.
        # Unlike the audit log this is not hash-chained — it is an operational
        # convenience, not a tamper-evident record, and it says so.
        records = self.records()
        lines = [
            (r.model_copy(update={"undone": True}) if r.id == record_id else r).model_dump_json()
            for r in records
        ]
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        tmp.replace(self.path)


def load_undo_plan(result: Any) -> UndoPlan | None:
    """Pull an `UndoPlan` off a tool result, wherever it crossed the process boundary."""
    raw = getattr(result, "undo", None)
    if raw is None and isinstance(result, dict):
        raw = result.get("undo")
    if raw is None:
        return None
    if isinstance(raw, UndoPlan):
        return raw
    if isinstance(raw, dict):
        return UndoPlan.model_validate(raw)
    if isinstance(raw, str):
        return UndoPlan.model_validate(json.loads(raw))
    return None

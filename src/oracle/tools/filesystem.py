"""Write tools: `fs.write`, `fs.patch`, `fs.move`, `fs.delete`.

Every one of these backs up before it mutates and reports an `UndoPlan`, which is what
earns them the T1 "run automatically" tier (ADR-0005). A write tool that could not be
reversed would have to prompt, and prompting on every file edit is how an agent becomes
unusable.

`fs.delete` never unlinks user data — it moves to the trash. An unrecoverable delete is
T4 and is simply absent from the catalogue.
"""

from __future__ import annotations

import difflib
import os
import shutil
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from oracle.config import get_settings
from oracle.logsink import get_logger
from oracle.policy.model import Capability, Tier
from oracle.tools.contract import ToolArgs, ToolResult, tool
from oracle.tools.undo import TrashStore, UndoKind, UndoPlan

log = get_logger(__name__)

MAX_WRITE_BYTES = 2 * 1024 * 1024

ScopedPath = Annotated[str, Field(description="Absolute path inside an allowed scope")]


def _trash() -> TrashStore:
    return TrashStore(get_settings().trash_dir)


def _guard_size(text: str) -> None:
    size = len(text.encode("utf-8"))
    if size > MAX_WRITE_BYTES:
        raise ValueError(f"refusing to write {size} bytes (cap {MAX_WRITE_BYTES})")


# ----------------------------------------------------------------------- fs.write


class FsWriteArgs(ToolArgs):
    path: ScopedPath
    content: str
    #: Refuse rather than silently create. Defaults to allowing creation because that
    #: is the common case, but a caller that means "edit an existing file" can say so.
    create: bool = True


class FsWriteResult(ToolResult):
    path: str
    bytes_written: int
    created: bool
    undo: UndoPlan


@tool(
    id="fs.write",
    summary="Write text to a file, backing up any previous version first.",
    args=FsWriteArgs,
    result=FsWriteResult,
    capabilities={Capability.FS_WRITE},
    scopes={"projects", "notes", "scratch"},
    risk=Tier.T1,
    reversible=True,
    undo="restore the backed-up previous version",
    intents={"modify"},
    side_effects="Replaces the file's contents. The previous version goes to the trash.",
    path_fields={"path"},
)
async def fs_write(*, resolved: dict[str, Any], args: FsWriteArgs) -> FsWriteResult:
    real: Path = resolved["path"]
    _guard_size(args.content)

    existed = real.exists()
    if not existed and not args.create:
        raise ValueError(f"{real} does not exist and create=False")
    if existed and real.is_dir():
        raise ValueError(f"{real} is a directory")

    # Back up BEFORE mutating. A crash between the two leaves an unreferenced backup,
    # which is harmless; the opposite ordering loses the file.
    plan = _trash().back_up(real)

    real.parent.mkdir(parents=True, exist_ok=True)
    data = args.content.encode("utf-8")
    # Write to a sibling temp then replace: an interrupted write must not leave a
    # half-written file where a whole one used to be.
    tmp = real.with_name(real.name + ".oracle-tmp")
    tmp.write_bytes(data)
    os.replace(tmp, real)

    return FsWriteResult(path=str(real), bytes_written=len(data), created=not existed, undo=plan)


# ----------------------------------------------------------------------- fs.patch


class FsPatchArgs(ToolArgs):
    path: ScopedPath
    #: Exact text to replace. Preferred over a whole-file write: smaller, reviewable,
    #: and it fails loudly when the file is not what the caller thought it was.
    find: str
    replace: str
    count: int = 1


class FsPatchResult(ToolResult):
    path: str
    replacements: int
    diff: str
    undo: UndoPlan


@tool(
    id="fs.patch",
    summary="Replace exact text in a file. Fails if the text is absent or ambiguous.",
    args=FsPatchArgs,
    result=FsPatchResult,
    capabilities={Capability.FS_WRITE},
    scopes={"projects", "notes", "scratch"},
    risk=Tier.T1,
    reversible=True,
    undo="restore the backed-up previous version",
    intents={"modify"},
    side_effects="Edits the file in place. The previous version goes to the trash.",
    path_fields={"path"},
)
async def fs_patch(*, resolved: dict[str, Any], args: FsPatchArgs) -> FsPatchResult:
    real: Path = resolved["path"]
    if not real.exists():
        raise ValueError(f"{real} does not exist")

    original = real.read_text(encoding="utf-8")
    occurrences = original.count(args.find)
    if occurrences == 0:
        raise ValueError(f"text not found in {real.name}; the file is not what was expected")
    if args.count > 0 and occurrences > args.count:
        # Ambiguity is an error, not something to resolve by guessing which one.
        raise ValueError(
            f"found {occurrences} occurrences but count={args.count}; "
            "make the search text more specific"
        )

    updated = (
        original.replace(args.find, args.replace)
        if args.count <= 0
        else original.replace(args.find, args.replace, args.count)
    )
    _guard_size(updated)

    plan = _trash().back_up(real)
    tmp = real.with_name(real.name + ".oracle-tmp")
    tmp.write_text(updated, encoding="utf-8")
    os.replace(tmp, real)

    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"a/{real.name}",
            tofile=f"b/{real.name}",
            n=2,
        )
    )
    return FsPatchResult(
        path=str(real),
        replacements=occurrences if args.count <= 0 else min(occurrences, args.count),
        diff=diff[:4000],
        undo=plan,
    )


# ------------------------------------------------------------------------ fs.move


class FsMoveArgs(ToolArgs):
    path: ScopedPath
    destination: ScopedPath


class FsMoveResult(ToolResult):
    path: str
    destination: str
    undo: UndoPlan


@tool(
    id="fs.move",
    summary="Move or rename a file. Refuses to overwrite an existing destination.",
    args=FsMoveArgs,
    result=FsMoveResult,
    capabilities={Capability.FS_WRITE},
    scopes={"projects", "notes", "scratch"},
    risk=Tier.T1,
    reversible=True,
    undo="move the file back to where it came from",
    intents={"modify"},
    side_effects="Moves a file. Nothing is overwritten.",
    # BOTH paths are resolved and scope-checked. Resolving only the source would let a
    # move write anywhere on disk.
    path_fields={"path", "destination"},
)
async def fs_move(*, resolved: dict[str, Any], args: FsMoveArgs) -> FsMoveResult:
    src: Path = resolved["path"]
    dst: Path = resolved["destination"]
    if not src.exists():
        raise ValueError(f"{src} does not exist")
    if dst.exists():
        raise ValueError(f"{dst} already exists; refusing to overwrite")

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    return FsMoveResult(
        path=str(src),
        destination=str(dst),
        undo=UndoPlan(kind=UndoKind.MOVE_BACK, target=str(dst), origin=str(src)),
    )


# ---------------------------------------------------------------------- fs.delete


class FsDeleteArgs(ToolArgs):
    path: ScopedPath
    #: Required for a directory. Being explicit is the point: a recursive delete should
    #: never be something the caller did without saying so.
    recursive: bool = False


class FsDeleteResult(ToolResult):
    path: str
    kind: str
    entries: int
    trashed_to: str
    undo: UndoPlan


@tool(
    id="fs.delete",
    summary="Move a file or directory to the trash. Never an unrecoverable delete.",
    args=FsDeleteArgs,
    result=FsDeleteResult,
    capabilities={Capability.FS_DELETE},
    scopes={"projects", "notes", "scratch"},
    risk=Tier.T3,
    reversible=True,
    undo="restore from the trash",
    dry_run=True,
    intents={"modify"},
    side_effects="Moves the target into ORACLE's trash. Recoverable until the trash is emptied.",
    path_fields={"path"},
)
async def fs_delete(*, resolved: dict[str, Any], args: FsDeleteArgs) -> FsDeleteResult:
    real: Path = resolved["path"]
    if not real.exists():
        raise ValueError(f"{real} does not exist")

    is_dir = real.is_dir()
    entries = sum(1 for _ in real.rglob("*")) if is_dir else 1
    if is_dir and not args.recursive:
        raise ValueError(f"{real} is a directory; pass recursive=true to delete it")

    plan = _trash().move_to_trash(real)
    return FsDeleteResult(
        path=str(real),
        kind="dir" if is_dir else "file",
        entries=entries,
        trashed_to=plan.backup or "",
        undo=plan,
    )


WRITE_TOOLS = [fs_write, fs_patch, fs_move, fs_delete]

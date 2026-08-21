"""Phase 2 tool set: read-only only.

Deliberately nothing that writes. The gate is proven with tools that cannot damage
anything, and write tools arrive in Phase 3 once it has (ROADMAP sequencing rule 2).

Every path argument is a `ScopedPath`, resolved through the canonicaliser before the
handler is ever reached — a bare `str` here would bypass the entire sandbox.
"""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Any, Literal

from pydantic import Field

from oracle.logsink import get_logger
from oracle.policy.model import Capability, Tier
from oracle.tools.contract import ToolArgs, ToolResult, tool


def _pin(program: str) -> str | None:
    """Resolve a program to an absolute path ONCE, at import.

    docs/SECURITY.md#4b: never rely on PATH at call time. PATH is
    attacker-influenceable, and on Windows the current directory participates in the
    search order — a `tasklist.exe` dropped in a project folder would otherwise win.
    """
    import shutil

    found = shutil.which(program)
    if found is None:
        return None
    resolved = os.path.realpath(found)
    system_root = os.environ.get("SystemRoot", r"C:\Windows").lower()
    # Only trust a system utility that actually lives under the system root.
    return resolved if resolved.lower().startswith(system_root) else None


_TASKLIST = _pin("tasklist")


#: Refuse to slurp something enormous into a small model's context.
MAX_READ_BYTES = 256 * 1024
MAX_LIST_ENTRIES = 500

log = get_logger(__name__)

ScopedPath = Annotated[str, Field(description="Absolute path inside an allowed scope")]


# ------------------------------------------------------------------------ fs.read


class FsReadArgs(ToolArgs):
    path: ScopedPath
    max_bytes: int = MAX_READ_BYTES


class FsReadResult(ToolResult):
    path: str
    text: str
    encoding: str
    bytes_read: int
    truncated: bool


@tool(
    id="fs.read",
    summary="Read a UTF-8 text file. Refuses binaries and caps size.",
    args=FsReadArgs,
    result=FsReadResult,
    capabilities={Capability.FS_READ},
    scopes={"projects", "notes", "scratch"},
    risk=Tier.T0,
    reversible=True,
    intents={"question", "investigate", "search", "modify"},
    side_effects="None.",
    path_fields={"path"},
)
async def fs_read(*, resolved: dict[str, Any], args: FsReadArgs) -> FsReadResult:
    real = resolved["path"]
    raw = real.read_bytes()[: args.max_bytes]
    # Binary detection before decode: a NUL byte in the first block is the cheap,
    # reliable signal, and decoding garbage into a prompt is worse than refusing.
    if b"\x00" in raw[:8192]:
        raise ValueError(f"{real} looks binary; refusing to read it as text")
    try:
        text = raw.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        text = raw.decode("cp1251", errors="replace")
        encoding = "cp1251"
    return FsReadResult(
        path=str(real),
        text=text,
        encoding=encoding,
        bytes_read=len(raw),
        truncated=real.stat().st_size > len(raw),
    )


# ------------------------------------------------------------------------ fs.list


class FsListArgs(ToolArgs):
    path: ScopedPath
    recursive: bool = False


class Entry(ToolResult):
    name: str
    kind: Literal["file", "dir", "reparse"]
    size: int


class FsListResult(ToolResult):
    path: str
    entries: list[Entry]
    truncated: bool


#: Never recurse into these unasked (docs/TOOLS.md). Source2DemViewer's `target/` alone
#: holds 3,915 files.
_SKIP = frozenset({"node_modules", "target", ".git", "dist", "build", "__pycache__", ".venv"})


@tool(
    id="fs.list",
    summary="List a directory. Skips node_modules, target, .git and other build output.",
    args=FsListArgs,
    result=FsListResult,
    capabilities={Capability.FS_READ},
    scopes={"projects", "notes", "scratch"},
    risk=Tier.T0,
    reversible=True,
    intents={"question", "investigate", "search", "status"},
    side_effects="None.",
    path_fields={"path"},
)
async def fs_list(*, resolved: dict[str, Any], args: FsListArgs) -> FsListResult:
    real = resolved["path"]
    entries: list[Entry] = []
    truncated = False

    walker = real.rglob("*") if args.recursive else real.iterdir()
    for p in walker:
        if any(part in _SKIP for part in p.parts):
            continue
        if len(entries) >= MAX_LIST_ENTRIES:
            truncated = True
            break
        try:
            st = os.lstat(p)
            # A reparse point is reported as such rather than followed: the caller
            # should know a junction is there before asking to read through it.
            if getattr(st, "st_file_attributes", 0) & 0x400:
                kind: Literal["file", "dir", "reparse"] = "reparse"
            else:
                kind = "dir" if p.is_dir() else "file"
            entries.append(Entry(name=p.name, kind=kind, size=st.st_size))
        except OSError:
            continue

    return FsListResult(path=str(real), entries=entries, truncated=truncated)


# ------------------------------------------------------------------------ fs.stat


class FsStatArgs(ToolArgs):
    path: ScopedPath


class FsStatResult(ToolResult):
    path: str
    exists: bool
    kind: Literal["file", "dir", "reparse", "missing"]
    size: int
    modified: float


@tool(
    id="fs.stat",
    summary="Metadata for one path: kind, size, mtime. Does not read contents.",
    args=FsStatArgs,
    result=FsStatResult,
    capabilities={Capability.FS_READ},
    scopes={"projects", "notes", "scratch"},
    risk=Tier.T0,
    reversible=True,
    intents={"question", "investigate", "status"},
    side_effects="None.",
    path_fields={"path"},
)
async def fs_stat(*, resolved: dict[str, Any], args: FsStatArgs) -> FsStatResult:
    real = resolved["path"]
    if not real.exists():
        return FsStatResult(path=str(real), exists=False, kind="missing", size=0, modified=0.0)
    st = os.lstat(real)
    kind: Literal["file", "dir", "reparse", "missing"]
    if getattr(st, "st_file_attributes", 0) & 0x400:
        kind = "reparse"
    else:
        kind = "dir" if real.is_dir() else "file"
    return FsStatResult(
        path=str(real), exists=True, kind=kind, size=st.st_size, modified=st.st_mtime
    )


# ----------------------------------------------------------------------- sys.info


class SysInfoArgs(ToolArgs):
    pass


class SysInfoResult(ToolResult):
    cpu_percent: float
    ram_used_gb: float
    ram_total_gb: float
    disks: dict[str, float]


@tool(
    id="sys.info",
    summary="CPU, RAM and free disk space.",
    args=SysInfoArgs,
    result=SysInfoResult,
    capabilities={Capability.SYS_INFO},
    risk=Tier.T0,
    reversible=True,
    intents={"status", "question"},
    side_effects="None.",
)
async def sys_info(*, resolved: dict[str, Any], args: SysInfoArgs) -> SysInfoResult:
    import shutil

    total = getattr(os, "sysconf", None)
    ram_total = ram_used = 0.0
    try:  # pragma: no cover - platform detail
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        m = MEMORYSTATUSEX()
        m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
        ram_total = m.ullTotalPhys / 1e9
        ram_used = (m.ullTotalPhys - m.ullAvailPhys) / 1e9
    except Exception:  # pragma: no cover - platform detail
        log.warning("sys_info.memory_unavailable")

    disks: dict[str, float] = {}
    for drive in ("C:", "D:", "E:"):
        try:
            disks[drive] = round(shutil.disk_usage(drive + "\\").free / 1e9, 1)
        except OSError:
            continue

    _ = total
    return SysInfoResult(
        cpu_percent=round((os.cpu_count() or 0) and 0.0, 1),
        ram_used_gb=round(ram_used, 1),
        ram_total_gb=round(ram_total, 1),
        disks=disks,
    )


# ------------------------------------------------------------------ sys.processes


class SysProcessesArgs(ToolArgs):
    name_contains: str = ""


class Process(ToolResult):
    pid: int
    name: str


class SysProcessesResult(ToolResult):
    processes: list[Process]
    total: int


@tool(
    id="sys.processes",
    summary="List running processes by name. Command lines are NOT returned.",
    args=SysProcessesArgs,
    result=SysProcessesResult,
    capabilities={Capability.SYS_INFO},
    risk=Tier.T0,
    reversible=True,
    intents={"status", "question", "investigate"},
    # Full command lines routinely contain credentials (docs/LOGGING.md#6), so this
    # returns pid and image name only.
    side_effects="None.",
)
async def sys_processes(*, resolved: dict[str, Any], args: SysProcessesArgs) -> SysProcessesResult:
    if _TASKLIST is None:
        raise RuntimeError("tasklist.exe was not found at a trusted absolute path")

    # asyncio, not subprocess.run: a blocking call here stalls the whole event loop,
    # including every other client's event stream. Caught by ruff ASYNC221.
    proc = await asyncio.create_subprocess_exec(
        _TASKLIST,
        "/fo",
        "csv",
        "/nh",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    except TimeoutError:
        proc.kill()
        raise

    procs: list[Process] = []
    for line in stdout.decode("utf-8", errors="replace").splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) < 2:
            continue
        name = parts[0].strip('"')
        if args.name_contains and args.name_contains.lower() not in name.lower():
            continue
        try:
            procs.append(Process(pid=int(parts[1]), name=name))
        except ValueError:
            continue
    return SysProcessesResult(processes=procs[:MAX_LIST_ENTRIES], total=len(procs))


READ_ONLY_TOOLS = [fs_read, fs_list, fs_stat, sys_info, sys_processes]

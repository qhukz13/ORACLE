"""Phase 2 tool set: read-only only.

Deliberately nothing that writes. The gate is proven with tools that cannot damage
anything, and write tools arrive in Phase 3 once it has (ROADMAP sequencing rule 2).

Every path argument is a `ScopedPath`, resolved through the canonicaliser before the
handler is ever reached — a bare `str` here would bypass the entire sandbox.
"""

from __future__ import annotations

import asyncio
import os
from typing import Annotated, Literal

from pydantic import Field

from oracle.logsink import get_logger
from oracle.policy.model import Capability, Tier
from oracle.tools.contract import ToolArgs, ToolContext, ToolResult, tool

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
async def fs_read(*, ctx: ToolContext, args: FsReadArgs) -> FsReadResult:
    real = ctx.resolved["path"]
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
async def fs_list(*, ctx: ToolContext, args: FsListArgs) -> FsListResult:
    real = ctx.resolved["path"]
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
async def fs_stat(*, ctx: ToolContext, args: FsStatArgs) -> FsStatResult:
    real = ctx.resolved["path"]
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
async def sys_info(*, ctx: ToolContext, args: SysInfoArgs) -> SysInfoResult:
    import shutil

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

    return SysInfoResult(
        cpu_percent=await _cpu_percent(),
        ram_used_gb=round(ram_used, 1),
        ram_total_gb=round(ram_total, 1),
        disks=disks,
    )


async def _cpu_percent(sample_ms: int = 120) -> float:
    """System-wide CPU load, from two `GetSystemTimes` samples.

    CPU utilisation is a rate, so it cannot be read instantaneously — it needs two
    samples and an interval. An earlier version returned a hardcoded 0.0, which is
    worse than returning nothing: a plausible-looking number that is always wrong.
    120 ms is short enough to be unnoticeable in a status call and long enough to be
    meaningful.
    """
    import ctypes
    from ctypes import wintypes

    class FILETIME(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        @property
        def value(self) -> int:
            return int(self.high) << 32 | int(self.low)

    def sample() -> tuple[int, int]:
        idle, kernel, user = FILETIME(), FILETIME(), FILETIME()
        if not ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        ):
            raise OSError("GetSystemTimes failed")
        # kernel time includes idle time, so total is kernel + user.
        return idle.value, kernel.value + user.value

    try:
        idle0, total0 = sample()
        await asyncio.sleep(sample_ms / 1000)
        idle1, total1 = sample()
    except OSError:
        log.warning("sys_info.cpu_unavailable")
        return 0.0

    d_total = total1 - total0
    if d_total <= 0:
        return 0.0
    busy = 1.0 - (idle1 - idle0) / d_total
    return round(max(0.0, min(1.0, busy)) * 100, 1)


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
    capabilities={Capability.SYS_INFO, Capability.PROC_SPAWN},
    risk=Tier.T0,
    reversible=True,
    intents={"status", "question", "investigate"},
    # Full command lines routinely contain credentials (docs/LOGGING.md#6), so this
    # returns pid and image name only.
    side_effects="None.",
    programs={"tasklist"},
)
async def sys_processes(*, ctx: ToolContext, args: SysProcessesArgs) -> SysProcessesResult:
    # The path was pinned by the PARENT from the program allowlist and handed over. This
    # process never looks a program up: doing so would put the decision on the wrong
    # side of the boundary, exactly as resolving a path here would (ADR-0003).
    #
    # asyncio, not subprocess.run: a blocking call here stalls the whole event loop,
    # including every other client's event stream. Caught by ruff ASYNC221.
    proc = await asyncio.create_subprocess_exec(
        str(ctx.program("tasklist")),
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


#: Tools that touch nothing and spawn nothing. This is what a read-only deployment
#: gets, and the security suite asserts that none of them holds a writing capability.
READ_ONLY_TOOLS = [fs_read, fs_list, fs_stat, sys_info]

#: Reads state, but does it by spawning a process. `proc.spawn` is a writing capability
#: even though `tasklist` changes nothing — under-declaring it to keep the tool in the
#: read-only bucket would be exactly the silent privilege gap the registry checks for.
#: The gate refuses `proc.spawn` in lockdown anyway, so offering this in a read-only
#: build would only advertise something that could never run.
SPAWNING_READ_TOOLS = [sys_processes]

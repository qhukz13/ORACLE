"""Windows Job Objects — the only reliable way to kill a process tree.

This is the mechanism HALT's credibility rests on. `Popen.kill()` terminates one
process; `npm install`'s grandchildren survive it happily. A Job Object with
`JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` kills every process in the job when the last
handle closes — including when the parent is force-killed, because the OS closes its
handles for us.

The same mechanism is already proven in the Tauri shell
(`apps/desktop/src-tauri/src/backend.rs`, OQ-11): force-quitting the shell took
`oracled` with it. This is that pattern, in Python, one level down.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from oracle.logsink import get_logger

log = get_logger(__name__)

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
JobObjectExtendedLimitInformation = 9


class IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.POINTER(wintypes.ULONG)),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class JobObjectError(OSError):
    pass


class JobObject:
    """A kill-on-close job. Membership is inherited, so anything the child spawns —
    and anything *those* spawn — is in the job too."""

    def __init__(
        self,
        *,
        max_processes: int = 64,
        max_process_memory_mb: int = 4096,
    ) -> None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32 = kernel32

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise JobObjectError(f"CreateJobObjectW failed: {ctypes.get_last_error()}")
        self._handle: int | None = handle

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            | JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | JOB_OBJECT_LIMIT_PROCESS_MEMORY
        )
        # A runaway fork bomb or a memory-hungry build should hit a wall inside the
        # job rather than take the machine down with it.
        info.BasicLimitInformation.ActiveProcessLimit = max_processes
        info.ProcessMemoryLimit = max_process_memory_mb * 1024 * 1024

        ok = kernel32.SetInformationJobObject(
            wintypes.HANDLE(handle),
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            err = ctypes.get_last_error()
            self.close()
            raise JobObjectError(f"SetInformationJobObject failed: {err}")

    def assign(self, process_handle: int) -> None:
        """Put a process in the job.

        If this fails we must not continue: an unassigned child could spawn a tree we
        cannot guarantee to kill, which is precisely the failure the job exists to
        prevent.
        """
        if self._handle is None:
            raise JobObjectError("job object is closed")
        ok = self._k32.AssignProcessToJobObject(
            wintypes.HANDLE(self._handle), wintypes.HANDLE(process_handle)
        )
        if not ok:
            raise JobObjectError(f"AssignProcessToJobObject failed: {ctypes.get_last_error()}")

    def terminate(self, exit_code: int = 1) -> None:
        """Kill every process in the job, right now."""
        if self._handle is None:
            return
        self._k32.TerminateJobObject(wintypes.HANDLE(self._handle), wintypes.UINT(exit_code))

    def close(self) -> None:
        """Closing the last handle triggers KILL_ON_JOB_CLOSE."""
        if self._handle is not None:
            self._k32.CloseHandle(wintypes.HANDLE(self._handle))
            self._handle = None

    def __enter__(self) -> JobObject:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

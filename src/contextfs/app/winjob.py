"""A Windows Job Object for the indexer child (ADR-0001 §2 and Revision 1).

- ``KILL_ON_JOB_CLOSE``: the job handle lives in the GUI process. If the GUI exits or crashes,
  Windows closes the handle and kills the child, so an orphaned writer can never keep the index
  locked.
- A per-process memory limit (2 GB, or 1.5 GB on machines with 8 GB of RAM or less): a parser
  that balloons fails its allocation instead of exhausting the machine. The engine's
  strike/quarantine turns a repeat into a skipped file.
- Below-normal CPU priority, so the window and the rest of the machine stay responsive.

``peak_memory`` reads the job's accounting (used by the hardening suite). Off Windows,
everything here is a no-op.
"""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

__all__ = ["ChildJob", "default_memory_limit"]

_JobObjectExtendedLimitInformation = 9
_KILL_ON_JOB_CLOSE = 0x2000
_LIMIT_PROCESS_MEMORY = 0x100
_PROCESS_SET_QUOTA, _PROCESS_TERMINATE, _PROCESS_SET_INFORMATION = 0x0100, 0x0001, 0x0200
_BELOW_NORMAL_PRIORITY_CLASS = 0x4000


class _IO_COUNTERS(ctypes.Structure):  # noqa: N801 - Windows name
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BASIC_LIMIT(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _EXTENDED_LIMIT(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("BasicLimitInformation", _BASIC_LIMIT),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _MEMSTATUS(ctypes.Structure):
    _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]


def default_memory_limit() -> int:
    """2 GB, or 1.5 GB when the machine has 8 GB of RAM or less (ADR-0001 Rev. 1)."""
    if sys.platform != "win32":
        return 2 * 2**30
    st = _MEMSTATUS()
    st.dwLength = ctypes.sizeof(st)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(st))
    return int(1.5 * 2**30) if st.ullTotalPhys <= 8 * 2**30 else 2 * 2**30


class ChildJob:
    """Holds a job; :meth:`assign` puts a child process (by pid) into it."""

    def __init__(self, memory_limit: int | None = None) -> None:
        """Create the job (no-op off Windows or on any failure)."""
        self.handle = None
        self.memory_limit = memory_limit or default_memory_limit()
        if sys.platform != "win32":
            return
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        handle = k32.CreateJobObjectW(None, None)
        if not handle:
            return
        info = _EXTENDED_LIMIT()
        info.BasicLimitInformation.LimitFlags = _KILL_ON_JOB_CLOSE | _LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = self.memory_limit
        k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        if not k32.SetInformationJobObject(handle, _JobObjectExtendedLimitInformation,
                                           ctypes.byref(info), ctypes.sizeof(info)):
            k32.CloseHandle(handle)
            return
        self.handle, self._k32 = handle, k32

    def assign(self, pid: int) -> bool:
        """Put process ``pid`` in the job and lower its priority. False if it could not be done."""
        if not self.handle or not pid:
            return False
        k32 = self._k32
        k32.OpenProcess.restype = wintypes.HANDLE
        proc = k32.OpenProcess(_PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_SET_INFORMATION, False, pid)
        if not proc:
            return False
        try:
            ok = bool(k32.AssignProcessToJobObject(wintypes.HANDLE(self.handle), wintypes.HANDLE(proc)))
            k32.SetPriorityClass(wintypes.HANDLE(proc), _BELOW_NORMAL_PRIORITY_CLASS)
            return ok
        finally:
            k32.CloseHandle(wintypes.HANDLE(proc))

    def peak_memory(self) -> int:
        """Peak private memory of any process in the job so far (bytes; 0 if unknown)."""
        if not self.handle:
            return 0
        info = _EXTENDED_LIMIT()
        k32 = self._k32
        k32.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
                                                  wintypes.DWORD, ctypes.c_void_p]
        if not k32.QueryInformationJobObject(self.handle, _JobObjectExtendedLimitInformation,
                                             ctypes.byref(info), ctypes.sizeof(info), None):
            return 0
        return int(info.PeakProcessMemoryUsed)

    def close(self) -> None:
        """Close the job (kills any process still in it)."""
        if self.handle:
            self._k32.CloseHandle(wintypes.HANDLE(self.handle))
            self.handle = None

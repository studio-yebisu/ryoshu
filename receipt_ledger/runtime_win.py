"""Windows ownership primitives for the owned llama-server child (ctypes only).

Job objects make the child die with the app; the TCP owner table proves that
the listener we are about to authenticate to belongs to our own child PID.
Other platforms are unsupported: ownership is never assumed there.
"""
import ctypes
import os
import socket

WINDOWS = os.name == "nt"
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TCP_TABLE_OWNER_PID_LISTENER = 3
ERROR_INSUFFICIENT_BUFFER = 122
STILL_ACTIVE = 259

if WINDOWS:
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    iphlpapi = ctypes.WinDLL("iphlpapi")

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD), ("SchedulingClass", wintypes.DWORD)]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    iphlpapi.GetExtendedTcpTable.restype = wintypes.DWORD
    iphlpapi.GetExtendedTcpTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL,
                                             wintypes.ULONG, ctypes.c_int, wintypes.ULONG]


class Job:
    """A kill-on-close job; closing the handle (or the app dying) ends the child."""

    def __init__(self):
        if not WINDOWS:
            raise OSError("job objects require Windows")
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise ctypes.WinError(error)
        self.handle = handle

    def assign(self, process):
        if not kernel32.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        handle, self.handle = self.handle, None
        if handle:
            kernel32.CloseHandle(handle)


def creation_time(pid):
    """FILETIME creation stamp of a live process, or None."""
    if not WINDOWS:
        return None
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        kernel32.CloseHandle(handle)


def running(pid):
    """True while pid has not exited (an exited process may still be openable via lingering handles)."""
    if not WINDOWS:
        return False
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _table(family):
    size = wintypes.DWORD(0)
    iphlpapi.GetExtendedTcpTable(None, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_LISTENER, 0)
    for _ in range(5):
        buffer = ctypes.create_string_buffer(max(size.value, 4))
        result = iphlpapi.GetExtendedTcpTable(buffer, ctypes.byref(size), False, family, TCP_TABLE_OWNER_PID_LISTENER, 0)
        if result == 0:
            return buffer.raw
        if result != ERROR_INSUFFICIENT_BUFFER:
            raise OSError(result, "GetExtendedTcpTable failed")
    raise OSError("TCP table kept growing")


def listeners(port):
    """[(address, pid)] for every TCP listener on port, IPv4 and IPv6."""
    if not WINDOWS:
        raise OSError("listener ownership requires Windows")
    found = []
    raw = _table(socket.AF_INET)
    for index in range(int.from_bytes(raw[:4], "little")):
        row = raw[4 + index * 24: 4 + (index + 1) * 24]
        if int.from_bytes(row[8:10], "big") == port:
            found.append((socket.inet_ntoa(row[4:8]), int.from_bytes(row[20:24], "little")))
    raw = _table(socket.AF_INET6)
    for index in range(int.from_bytes(raw[:4], "little")):
        row = raw[4 + index * 56: 4 + (index + 1) * 56]
        if int.from_bytes(row[20:22], "big") == port:
            found.append((socket.inet_ntop(socket.AF_INET6, row[:16]), int.from_bytes(row[52:56], "little")))
    return found


def owned_listener(port, pid):
    """True only if pid is the sole listener on port and listens on 127.0.0.1 only."""
    try:
        rows = listeners(port)
    except OSError:
        return False
    return bool(rows) and all(address == "127.0.0.1" and owner == pid for address, owner in rows)

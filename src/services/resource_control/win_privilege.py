"""Best-effort Windows token-privilege enabling.

Split out of ``windows_ops`` so the operator module stays focused on process
operations. The system-wide reclaim ops (empty working sets / flush standby)
try to enable ``SeProfileSingleProcessPrivilege`` before issuing the syscall.
"""

from __future__ import annotations

import ctypes

_kernel32 = ctypes.windll.kernel32
_advapi32 = ctypes.windll.advapi32

_TOKEN_ADJUST_PRIVILEGES = 0x0020
_TOKEN_QUERY = 0x0008
_SE_PRIVILEGE_ENABLED = 0x00000002

_kernel32.GetCurrentProcess.restype = ctypes.c_void_p


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]


class _LuidAndAttributes(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", ctypes.c_ulong)]


class _TokenPrivileges(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", ctypes.c_ulong),
        ("Privileges", _LuidAndAttributes * 1),
    ]


def try_enable_privilege(name: str) -> bool:
    """Best-effort enable a token privilege. Returns True only if actually enabled."""
    token = ctypes.c_void_p()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(),
        _TOKEN_ADJUST_PRIVILEGES | _TOKEN_QUERY,
        ctypes.byref(token),
    ):
        return False
    try:
        luid = _LUID()
        if not _advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False
        tp = _TokenPrivileges()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = _SE_PRIVILEGE_ENABLED
        if not _advapi32.AdjustTokenPrivileges(
            token, False, ctypes.byref(tp), ctypes.sizeof(tp), None, None,
        ):
            return False
        # AdjustTokenPrivileges returns success even if not all privs were
        # assigned. ERROR_NOT_ALL_ASSIGNED = 1300.
        return ctypes.GetLastError() != 1300
    finally:
        _kernel32.CloseHandle(token)

"""Shared constants for resource control."""

import psutil

MB = 1024 * 1024
GB = 1024 * 1024 * 1024
SYSTEM_MEMORY_LIST_INFORMATION = 0x50
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_SET_QUOTA = 0x0100
QUOTA_LIMITS_HARDWS_MIN_DISABLE = 0x00000002
QUOTA_LIMITS_HARDWS_MAX_DISABLE = 0x00000004

ACTIVITY_CACHE_TTL_SECONDS = 1800.0
MAX_REPORTED_ERRORS = 12

WARM_CPU_PERCENT = 5.0
HOT_CPU_PERCENT = 15.0
VERY_HOT_CPU_PERCENT = 45.0
WARM_DISK_MB_S = 4.0
HOT_DISK_MB_S = 24.0
VERY_HOT_DISK_MB_S = 96.0
WARM_OTHER_MB_S = 2.0
HOT_OTHER_MB_S = 8.0
VERY_HOT_OTHER_MB_S = 32.0
MIN_ESTIMATED_RECLAIM_MB = 32.0

TOP_STATUS_BONUS = {
    psutil.STATUS_SLEEPING: 1.20,
    psutil.STATUS_STOPPED: 1.20,
    psutil.STATUS_IDLE: 1.20,
    psutil.STATUS_RUNNING: 0.80,
}

WINDOWS_DIR = __import__("os").environ.get("WINDIR", r"C:\Windows").lower()
LOGICAL_CPU_COUNT = max(psutil.cpu_count() or 1, 1)

PROTECTED_NAMES = frozenset({
    "system", "system idle process", "registry", "smss.exe", "csrss.exe", "wininit.exe",
    "services.exe", "lsass.exe", "svchost.exe", "winlogon.exe", "dwm.exe",
    "fontdrvhost.exe", "explorer.exe", "taskmgr.exe", "shellexperiencehost.exe",
    "searchhost.exe", "startmenuexperiencehost.exe", "applicationframehost.exe",
    "memcompression", "memory compression", "secure system",
})
PROTECTED_USERS = frozenset({
    "nt authority\\system",
    "nt authority\\local service",
    "nt authority\\network service",
    "system",
})

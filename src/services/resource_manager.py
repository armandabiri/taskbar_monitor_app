"""Backward-compatible façade for the resource-control service."""

from services.resource_control import (
    AGGRESSIVE,
    BALANCED,
    CleanupMode,
    CleanupScope,
    GENTLE,
    NUCLEAR,
    ResourceProfile,
    load_active_aggressive_profile,
    load_active_smart_profile,
    plan_cleanup,
    release_resources,
)

__all__ = [
    "release_resources",
    "plan_cleanup",
    "ResourceProfile",
    "CleanupMode",
    "CleanupScope",
    "GENTLE",
    "BALANCED",
    "AGGRESSIVE",
    "NUCLEAR",
    "load_active_smart_profile",
    "load_active_aggressive_profile",
]

"""Backward-compatible façade for the resource-control service."""

from services.resource_control import (
    AGGRESSIVE,
    BALANCED,
    GENTLE,
    NUCLEAR,
    ResourceProfile,
    load_active_aggressive_profile,
    load_active_smart_profile,
    release_resources,
)

__all__ = [
    "release_resources",
    "ResourceProfile",
    "GENTLE",
    "BALANCED",
    "AGGRESSIVE",
    "NUCLEAR",
    "load_active_smart_profile",
    "load_active_aggressive_profile",
]

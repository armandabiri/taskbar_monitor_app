"""Backward-compatible façade for the resource-control service."""

from services.resource_control import release_resources

__all__ = ["release_resources"]

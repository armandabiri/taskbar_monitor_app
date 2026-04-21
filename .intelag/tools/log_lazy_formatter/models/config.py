"""
Log lazy formatter configuration model.

This module provides configuration classes for the log lazy formatter tool,
including settings for file processing, logging methods, and exclusions.
Cross-platform compatibility: Full support across Windows, macOS, and Linux.

╭────────────────────────────────────────────────────────────────────────────────────────╮
   ⚡ INTELAG PROPRIETARY CODE
   🏢 © 2025 Intelag LLC • All Rights Reserved
   📋 Licensed as Proprietary and Confidential
   ⚠️ Unauthorized use, reproduction, or distribution is strictly prohibited
   👤 Intelag LLC <intelag@outlook.com>
   🕒 Generated on: 2025-09-25 12:00:00
╰────────────────────────────────────────────────────────────────────────────────────────╯
"""

# Standard library imports
from typing import Any, Set

# Third-party imports
# (None)

# Internal imports
# Removed intelag_pkg_manager dependency


class LogLazyFormatterConfig:
    """Configuration for the Log Lazy Formatter tool."""

    # Attribute declarations for mypy
    default_exclude_dirs: Set[str]
    python_extensions: Set[str]
    logging_methods: Set[str]
    skip_methods: Set[str]

    def __init__(self, **_kwargs: Any) -> None:
        """Initialize configuration with provided values and defaults."""
        # Set defaults
        self.default_exclude_dirs = {
            ".venv",
            "venv",
            "env",
            "__pycache__",
            ".git",
            "node_modules",
            ".tox",
            "build",
            "dist",
            ".egg-info",
            ".mypy_cache",
            "htmlcov",
            "coverage_html",
            ".coverage",
            ".idea",
            ".vscode",
            "migrations",
            ".docker",
            "docker",
        }

        self.python_extensions = {".py", ".pyw"}

        self.logging_methods = {
            "debug",
            "info",
            "warning",
            "warn",
            "error",
            "exception",
            "critical",
            "fatal",
            "log",  # Generic log method
        }

        self.skip_methods = {
            "getLogger",
            "basicConfig",
            "disable",
            "addLevelName",
            "getLevelName",
            "setLoggerClass",
            "getLoggerClass",
            "captureWarnings",
            "shutdown",
            "addHandler",
            "removeHandler",
            "setLevel",
            "getEffectiveLevel",
            "isenableFor",
            "makeRecord",
            "handle",
            "addFilter",
            "removeFilter",
            "filter",
            "callHandlers",
            "getChild",
            "hasHandlers",
        }

        # Custom validation
        self._custom_validation()

    def _custom_validation(self) -> None:
        """Custom validation for log lazy formatter configuration."""
        # Ensure python extensions start with dot
        self.python_extensions = {
            ext if ext.startswith(".") else f".{ext}" for ext in self.python_extensions
        }

        # Ensure all extensions are lowercase
        self.python_extensions = {ext.lower() for ext in self.python_extensions}

        # Ensure all methods are lowercase
        self.logging_methods = {method.lower() for method in self.logging_methods}
        self.skip_methods = {method.lower() for method in self.skip_methods}

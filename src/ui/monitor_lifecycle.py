"""MonitorLifecycle — single owner of stop/join for timers, threads, handles.

T01 establishes the registry; T13 wires every timer/thread/native-handle
through it so ``closeEvent``/``aboutToQuit`` produces a deterministic, race-free
teardown. ``shutdown`` is idempotent so re-entrancy from a second close event
is safe.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

LOGGER = logging.getLogger(__name__)


@dataclass
class _Stoppable:
    name: str
    stop: Callable[[], None]
    join: Callable[[float], None] | None = None


class MonitorLifecycle:
    """Ordered registry of things that must be stopped on shutdown."""

    def __init__(self) -> None:
        self._items: list[_Stoppable] = []
        self._shut_down = False

    def register(
        self,
        name: str,
        stop: Callable[[], None],
        join: Callable[[float], None] | None = None,
    ) -> None:
        """Register a stoppable. Registration order = shutdown order."""
        self._items.append(_Stoppable(name=name, stop=stop, join=join))

    def shutdown(self, timeout_ms: int = 2000) -> None:
        """Stop everything in registration order, then join with a budget.

        Idempotent: a second call is a no-op so closeEvent/aboutToQuit can
        both fire without double-finalizing native handles.
        """
        if self._shut_down:
            return
        self._shut_down = True
        deadline = time.monotonic() + max(0.0, timeout_ms / 1000.0)
        for item in self._items:
            try:
                item.stop()
            except Exception:  # pylint: disable=broad-exception-caught
                LOGGER.exception("Lifecycle stop failed for %s", item.name)
        for item in self._items:
            if item.join is None:
                continue
            remaining = max(0.0, deadline - time.monotonic())
            try:
                item.join(remaining)
            except Exception:  # pylint: disable=broad-exception-caught
                LOGGER.exception("Lifecycle join failed for %s", item.name)

    @property
    def is_shut_down(self) -> bool:
        return self._shut_down

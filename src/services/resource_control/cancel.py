"""Cooperative cancellation for cleanup runs.

The cleanup service walks every process and performs trim/throttle/kill phases
that can take several seconds. A :class:`CancelToken` lets the UI ask an
in-flight run to stop promptly: the worker checks the token at safe points
(each scan batch and before each phase) and bails out cleanly.
"""

from __future__ import annotations

import threading


class CancelToken:
    """Thread-safe one-way cancel flag shared between the UI and worker.

    Set from the UI thread via :meth:`cancel`; polled from the worker thread
    via :attr:`cancelled`. Once cancelled it never un-cancels.
    """

    __slots__ = ("_event",)

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation (idempotent, thread-safe)."""
        self._event.set()

    @property
    def cancelled(self) -> bool:
        """True once :meth:`cancel` has been called."""
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until cancelled or ``timeout`` elapses. Returns the flag state."""
        return self._event.wait(timeout)

"""Run a native operation under a timeout watchdog.

System-wide flush calls (``NtSetSystemInformation`` with the memory-list
commands) can block the kernel for many seconds on a loaded box. A direct
call from the worker thread would freeze the cleanup run. ``WatchedSystemOp``
runs the call on a short-lived helper thread and reports ``timed_out`` if
it overruns its budget; the worker keeps moving either way.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class OpResult:
    """Outcome of one watched native op."""

    ok: bool
    timed_out: bool
    elapsed_s: float
    error: str | None = None


class WatchedSystemOp:
    """Wrap a callable in a timeout watchdog."""

    @staticmethod
    def run(fn: Callable[[], object], timeout_s: float) -> OpResult:
        start = time.monotonic()
        outcome: dict[str, object] = {}

        def _target() -> None:
            try:
                outcome["value"] = fn()
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                outcome["error"] = f"{type(exc).__name__}: {exc}"

        thread = threading.Thread(
            target=_target, name="WatchedSystemOp", daemon=True,
        )
        thread.start()
        thread.join(max(0.0, float(timeout_s)))
        elapsed = time.monotonic() - start
        if thread.is_alive():
            # Cannot kill the thread; let it finish in the background. The
            # caller treats this as a failed op and moves on.
            return OpResult(ok=False, timed_out=True, elapsed_s=elapsed)
        if "error" in outcome:
            return OpResult(
                ok=False, timed_out=False, elapsed_s=elapsed,
                error=str(outcome["error"]),
            )
        return OpResult(
            ok=bool(outcome.get("value")), timed_out=False, elapsed_s=elapsed,
        )

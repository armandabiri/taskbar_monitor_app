"""Progress reporting for a cleanup run.

A run moves through ordered phases (scan -> trim -> throttle -> kill -> flush).
The service emits a :class:`CleanupProgress` snapshot via an optional callback
so the UI can show what is happening instead of a frozen-looking button.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional


class CleanupPhase(str, Enum):
    """Ordered phases of a cleanup run, in execution order."""

    SCANNING = "scanning"
    TRIMMING = "trimming"
    THROTTLING = "throttling"
    KILLING = "killing"
    FLUSHING = "flushing"
    VERIFYING = "verifying"
    DONE = "done"


_PHASE_LABELS: dict[CleanupPhase, str] = {
    CleanupPhase.SCANNING: "Scanning processes",
    CleanupPhase.TRIMMING: "Trimming working sets",
    CleanupPhase.THROTTLING: "Throttling hot processes",
    CleanupPhase.KILLING: "Terminating processes",
    CleanupPhase.FLUSHING: "Flushing system caches",
    CleanupPhase.VERIFYING: "Measuring reclaimed memory",
    CleanupPhase.DONE: "Done",
}


@dataclass(frozen=True)
class CleanupProgress:
    """Immutable snapshot of run progress emitted to the UI."""

    phase: CleanupPhase
    scanned: int = 0
    total: Optional[int] = None
    executed: int = 0

    @property
    def phase_label(self) -> str:
        return _PHASE_LABELS.get(self.phase, self.phase.value.title())

    @property
    def fraction(self) -> float | None:
        """Scan completion in ``[0, 1]`` when a total is known, else ``None``."""
        if self.total is None or self.total <= 0:
            return None
        return max(0.0, min(1.0, self.scanned / self.total))


# Callback the service invokes with each progress snapshot. Must be cheap and
# thread-safe — it runs on the worker thread.
ProgressCallback = Callable[[CleanupProgress], None]


def emit(callback: ProgressCallback | None, progress: CleanupProgress) -> None:
    """Invoke ``callback`` with ``progress`` if one was supplied; never raises."""
    if callback is None:
        return
    try:
        callback(progress)
    except Exception:  # pylint: disable=broad-exception-caught
        # A misbehaving progress sink must never abort a cleanup run.
        pass

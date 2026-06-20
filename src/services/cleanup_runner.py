"""Run :func:`release_resources` on a background thread so the UI stays responsive.

``release_resources`` walks every running process via ``psutil.process_iter``
with many attributes, then performs trim/throttle/kill operations and Win32
working-set/standby-cache flushes. On a system with hundreds of processes
this takes 5–20 seconds and was previously executed on the UI thread,
freezing the whole app while it ran.

This module wraps the call in a ``QObject`` worker that lives on a ``QThread``,
and marshals the optional kill-confirmation dialog back to the UI thread via
a queued signal + ``threading.Event``.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Optional

from PyQt6.QtCore import QObject, pyqtSignal

from services.resource_control import CancelToken, CleanupScope, release_resources
from services.resource_control.models import ProcessCandidate
from services.resource_control.profiles import ResourceProfile

LOGGER = logging.getLogger(__name__)

# A UI-side function that shows the confirm-kill dialog and returns the
# approved list (or None to cancel). Receives the candidate list.
UiKillDialogFn = Callable[[list[ProcessCandidate]], Optional[list[ProcessCandidate]]]

# How long the worker will wait for the UI to respond to a kill request. If the
# UI is genuinely unresponsive (e.g. crashed), the worker times out and skips
# the kill phase rather than hang forever.
KILL_DIALOG_WAIT_TIMEOUT_S = 300.0


class _KillResponse:
    """Mutable holder used to ferry the dialog result back across threads."""

    __slots__ = ("event", "result")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: Optional[list[ProcessCandidate]] = None


class CleanupRunner(QObject):
    """Background worker that runs one ``release_resources`` call.

    Lifecycle: create, ``moveToThread(QThread)``, connect ``finished`` /
    ``failed`` / ``request_kill_dialog``, start the thread, call ``run``.
    The instance is single-use — create a new one per cleanup.
    """

    finished = pyqtSignal(object)       # emits ReleaseResult
    failed = pyqtSignal(object)         # emits Exception
    progress = pyqtSignal(object)       # emits CleanupProgress (queued to UI thread)
    # Queued connection delivers this on the UI thread. Handler must call
    # ``provide_kill_response`` (or set ``response.result`` and ``response.event``)
    # before returning. The third arg is the dialog title to display.
    request_kill_dialog = pyqtSignal(list, object, str)  # candidates, _KillResponse, title

    def __init__(
        self,
        *,
        profile: ResourceProfile,
        scope: CleanupScope | None = None,
        kill_dialog_title: str = "Confirm cleanup",
        force: bool = False,
        plan_only: bool = False,
    ) -> None:
        super().__init__()
        self._profile = profile
        self._scope = scope
        self._kill_dialog_title = kill_dialog_title
        self._force = force
        self._plan_only = plan_only
        self._cancel = CancelToken()

    def cancel(self) -> None:
        """Request cancellation of the in-flight run (thread-safe)."""
        self._cancel.cancel()

    # ------------------------------------------------------------------
    # Worker-thread entry point
    # ------------------------------------------------------------------
    def run(self) -> None:
        """Execute the cleanup. Must be invoked on the worker thread."""
        try:
            kill_callback = (
                self._confirm_kill
                if self._profile.enable_kill and self._profile.confirm_before_kill
                else None
            )
            result = release_resources(
                profile=self._profile,
                scope=self._scope,
                confirm_kill=kill_callback,
                force=self._force,
                plan_only=self._plan_only,
                cancel=self._cancel,
                progress=self.progress.emit,
            )
            self.finished.emit(result)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Resource release worker failed")
            self.failed.emit(exc)

    # ------------------------------------------------------------------
    # Cross-thread kill confirmation
    # ------------------------------------------------------------------
    def _confirm_kill(
        self, candidates: list[ProcessCandidate],
    ) -> Optional[list[ProcessCandidate]]:
        """Ask the UI to show the kill dialog. Blocks until the UI responds.

        Qt picks a queued connection automatically because the slot is bound
        to a QObject living on the UI thread, so the slot runs there while
        this worker thread blocks on the event.
        """
        response = _KillResponse()
        self.request_kill_dialog.emit(candidates, response, self._kill_dialog_title)
        if not response.event.wait(timeout=KILL_DIALOG_WAIT_TIMEOUT_S):
            LOGGER.warning(
                "Kill confirmation dialog did not respond within %.0fs; "
                "skipping kill phase.",
                KILL_DIALOG_WAIT_TIMEOUT_S,
            )
            return None
        return response.result


def provide_kill_response(
    response: Any,
    approved: Optional[list[ProcessCandidate]],
) -> None:
    """UI-side helper to deliver the dialog result back to the worker.

    Called from the slot connected to :attr:`CleanupRunner.request_kill_dialog`.
    """
    if isinstance(response, _KillResponse):
        response.result = approved
        response.event.set()

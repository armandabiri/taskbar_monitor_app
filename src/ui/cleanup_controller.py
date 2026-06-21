"""Owns the resource-cleanup user flow: worker lifecycle, progress, results."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QThread, QTimer

from core.config import APP_NAME
from services.cleanup_runner import CleanupRunner, provide_kill_response
from services.notification_service import NotificationService
from services.resource_control import (
    CleanupMode,
    CleanupScope,
    diff_snapshot_to_live,
    reset_throttled_processes,
)
from services.resource_control.runner_common import OPERATOR
from ui.cleanup_preview_dialog import CHOICE_FORCE, CHOICE_RUN, CleanupPreviewDialog
from ui.cleanup_progress_dialog import CleanupProgressDialog
from ui.cleanup_result_dialog import open_cleanup_result_dialog
from ui.kill_confirm_dialog import confirm_kill
from ui.snapshot_live_cleanup_dialog import select_snapshot_extra_processes

LOGGER = logging.getLogger(__name__)

WATCHDOG_GRACE_S = 5.0
TEARDOWN_WAIT_MS = 5000


class CleanupController:
    """Coordinates cleanup runs and their UI for the taskbar monitor."""

    def __init__(self, monitor) -> None:
        self._monitor = monitor
        self._in_flight = False
        self._thread: QThread | None = None
        self._runner: CleanupRunner | None = None
        self._progress = CleanupProgressDialog(monitor)
        self._progress.cancel_clicked.connect(self._on_cancel_clicked)
        self._watchdog_tripped = False

    @property
    def in_flight(self) -> bool:
        return self._in_flight

    # ------------------------------------------------------------------
    # Button-driven entry points
    # ------------------------------------------------------------------
    def request_release(self, *, aggressive: bool = False) -> None:
        if self._in_flight:
            return
        btn = self._monitor.aggressive_btn if aggressive else self._monitor.smart_btn
        old_text = btn.text() if btn is not None else ""
        if btn is not None:
            btn.setEnabled(False)
            btn.setText("⏳")
        profile = self._monitor._aggressive_profile if aggressive else self._monitor._smart_profile
        self.start(
            profile=profile, scope=None, mode_label=f"{profile.name} Clear",
            on_done_btn=btn, on_done_btn_text=old_text,
        )

    def force_reclaim(self) -> None:
        if self._in_flight:
            return
        profile = self._monitor._smart_profile
        self.start(
            profile=profile, scope=None,
            mode_label=f"{profile.name} Force Reclaim", force=True,
        )

    def preview_cleanup(self) -> None:
        if self._in_flight:
            return
        profile = self._monitor._smart_profile
        self.start(
            profile=profile, scope=None,
            mode_label=f"{profile.name} Preview", plan_only=True,
        )

    def auto_clean_fire(self) -> None:
        """Trigger a forced Smart cleanup from the auto-clean watchdog."""
        if self._in_flight:
            return
        profile = self._monitor._smart_profile
        NotificationService.notify(APP_NAME, "Auto-clean: memory under pressure — running cleanup.")
        self.start(profile=profile, scope=None, mode_label=f"{profile.name} Auto-Clean", force=True)

    def clean_using_snapshot(self, snapshot) -> None:
        """Preview + kill only the extra processes that appeared after a snapshot."""
        if self._in_flight:
            return
        profile = self._monitor._aggressive_profile
        try:
            diff = diff_snapshot_to_live(snapshot)
            selected_pids = select_snapshot_extra_processes(
                self._monitor.settings, diff, parent=self._monitor,
            )
            if selected_pids is None:
                return
            scope = CleanupScope(
                mode=CleanupMode.SNAPSHOT_EXTRAS.value,
                snapshot_name=snapshot.name,
                candidate_pids=frozenset(extra.pid for extra in diff.extra_processes),
                target_pids=frozenset(selected_pids),
                snapshot_matched_count=diff.matched_count,
                snapshot_identity_collisions=diff.identity_collisions,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Snapshot-driven cleanup setup failed")
            return
        self.start(
            profile=profile, scope=scope,
            mode_label=f"{profile.name} Clear (vs '{snapshot.name}')",
        )

    # ------------------------------------------------------------------
    # Standalone quick actions
    # ------------------------------------------------------------------
    def flush_standby_cache(self) -> None:
        """Purge the Windows standby cache directly, reporting success/failure."""
        try:
            ok = OPERATOR.flush_standby_cache()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Flush standby cache failed")
            NotificationService.notify(APP_NAME, f"Flush standby cache failed: {exc}")
            return
        if ok:
            NotificationService.notify(APP_NAME, "Standby cache flushed.")
        else:
            NotificationService.notify(
                APP_NAME,
                "Standby cache flush did not run — it usually needs administrator privileges.",
            )

    def reset_throttled(self) -> None:
        """Restore every process throttled by a previous cleanup."""
        restored, attempted = reset_throttled_processes()
        if attempted == 0:
            NotificationService.notify(APP_NAME, "No throttled processes to reset.")
        else:
            NotificationService.notify(
                APP_NAME, f"Restored {restored} of {attempted} throttled process(es).",
            )

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------
    def start(
        self,
        *,
        profile,
        scope,
        mode_label: str,
        force: bool = False,
        plan_only: bool = False,
        on_done_btn=None,
        on_done_btn_text: str = "",
    ) -> None:
        self._in_flight = True
        thread = QThread(self._monitor)
        runner = CleanupRunner(
            profile=profile,
            scope=scope,
            kill_dialog_title=f"Confirm {profile.name} Cleanup",
            force=force,
            plan_only=plan_only,
        )
        runner.moveToThread(thread)
        runner.request_kill_dialog.connect(self._on_kill_dialog_request)
        runner.progress.connect(self._progress.on_progress)
        runner.finished.connect(
            lambda result: self._on_done(
                result, profile, mode_label, force, plan_only, on_done_btn, on_done_btn_text,
                thread, runner,
            )
        )
        runner.failed.connect(
            lambda exc: self._on_failed(exc, on_done_btn, on_done_btn_text, thread, runner)
        )
        thread.started.connect(runner.run)
        self._thread = thread
        self._runner = runner
        self._watchdog_tripped = False
        self._progress.show_near_parent(self._monitor)
        thread.start()
        self._arm_watchdog(runner)

    def _arm_watchdog(self, runner: CleanupRunner) -> None:
        overrun_ms = int(max(0.0, runner.bounds.deadline_s + WATCHDOG_GRACE_S) * 1000)
        QTimer.singleShot(overrun_ms, lambda: self._on_watchdog_overrun(runner))

    def _on_watchdog_overrun(self, runner: CleanupRunner) -> None:
        if not self._in_flight or self._runner is not runner:
            return
        self._watchdog_tripped = True
        LOGGER.warning("Cleanup watchdog tripped — requesting cancel")
        try:
            runner.cancel()
            NotificationService.notify(
                APP_NAME, "Cleanup is taking too long — cancelling.",
            )
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Watchdog cancel/notify failed")

    def _on_cancel_clicked(self) -> None:
        if self._runner is not None:
            self._runner.cancel()

    def _on_kill_dialog_request(self, candidates, response, title: str) -> None:
        try:
            approved = confirm_kill(
                self._monitor, candidates, title=title, warning_prefix="background",
            )
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("confirm_kill dialog failed")
            approved = None
        provide_kill_response(response, approved)

    def _on_done(
        self, result, profile, mode_label, force, plan_only, btn, btn_text, thread, runner,
    ) -> None:
        del force
        self._progress.hide()
        # Tear the finished worker down first so any follow-up run (preview
        # confirm / escalate) starts from a clean, idle state.
        self._teardown(thread, runner)
        if btn is not None:
            QTimer.singleShot(600, lambda: self._restore_btn(btn, btn_text))
        if plan_only:
            self._present_preview(result, profile)
            return
        LOGGER.info("Resource release (%s): %s", profile.name, result.summary)
        self._monitor._last_release_error_count = len(result.errors)
        if btn is not None:
            tooltip = f"Last freed: {result.ram_freed_gb:.2f} GB"
            if result.errors:
                tooltip += f" — {len(result.errors)} error(s)"
            btn.setToolTip(tooltip)
        self._present_result(mode_label, result, profile)

    def _on_failed(self, exc, btn, btn_text, thread, runner) -> None:
        self._progress.hide()
        LOGGER.error("Resource release failed: %s", exc, exc_info=exc)
        if btn is not None:
            btn.setToolTip("Release failed — see log")
        self._teardown(thread, runner)
        if btn is not None:
            QTimer.singleShot(600, lambda: self._restore_btn(btn, btn_text))

    def _present_preview(self, result, profile) -> None:
        dialog = CleanupPreviewDialog(
            result, title=f"{profile.name} Preview", parent=self._monitor,
        )
        dialog.exec()
        if dialog.choice in (CHOICE_RUN, CHOICE_FORCE):
            self.start(
                profile=profile, scope=None,
                mode_label=f"{profile.name} Clear",
                force=dialog.choice == CHOICE_FORCE,
            )

    def _present_result(self, mode_name: str, result, profile) -> None:
        NotificationService.notify_cleanup(mode_name, result)
        on_escalate = None
        if result.mode == CleanupMode.SYSTEM_RECLAIM.value:
            on_escalate = lambda: self.start(  # noqa: E731
                profile=profile, scope=None,
                mode_label=f"{profile.name} Force Reclaim", force=True,
            )
        if result.errors or result.processes_cleaned_total == 0:
            title = f"{mode_name} Result"
            if result.errors:
                title = f"{mode_name} Result (partial)"
            open_cleanup_result_dialog(
                result, title=title, parent=self._monitor, on_escalate=on_escalate,
            )

    def _teardown(self, thread, runner) -> None:
        self._in_flight = False
        if thread is None:
            return
        try:
            thread.quit()
            if not thread.wait(TEARDOWN_WAIT_MS):
                LOGGER.error("Cleanup worker did not stop in %d ms", TEARDOWN_WAIT_MS)
        except RuntimeError:
            pass
        if runner is not None:
            runner.deleteLater()
        thread.deleteLater()
        if self._thread is thread:
            self._thread = None
            self._runner = None

    def _restore_btn(self, btn, text: str) -> None:
        btn.setText(text)
        btn.setEnabled(True)

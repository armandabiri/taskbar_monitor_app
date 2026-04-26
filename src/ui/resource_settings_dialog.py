"""Dialog for editing resource-control profiles and binding them to buttons."""

from __future__ import annotations

import logging
from dataclasses import fields
from typing import Callable

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from services.resource_control import (
    FLUSH_ALWAYS,
    FLUSH_CRITICAL_ONLY,
    FLUSH_MODES,
    FLUSH_NEVER,
    ResourceProfile,
    all_profiles,
    get_preset,
    load_active_aggressive_profile,
    load_active_smart_profile,
    load_profile,
    reset_custom_profile,
    save_custom_profile,
    set_active_aggressive_profile,
    set_active_smart_profile,
)

LOGGER = logging.getLogger(__name__)

_FLUSH_LABELS = {
    FLUSH_NEVER: "Never (lowest disk I/O)",
    FLUSH_CRITICAL_ONLY: "Only at critical pressure",
    FLUSH_ALWAYS: "Always (highest disk I/O)",
}

_DIALOG_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QGroupBox { color: #ddd; border: 1px solid #333; border-radius: 4px; margin-top: 12px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #aaa; }
QLabel { color: #ccc; }
QComboBox, QSpinBox, QDoubleSpinBox {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 2px 6px; min-width: 130px;
}
QComboBox QAbstractItemView { background-color: #2a2a2a; color: #eee; selection-background-color: #444; }
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 4px 12px;
}
QPushButton:hover { background-color: #3a3a3a; border-color: #55efc4; }
QPushButton:pressed { background-color: #4a4a4a; }
QCheckBox { color: #ccc; }
QFrame#hline { background-color: #333; }
"""


class ResourceSettingsDialog(QDialog):
    """Lets the user pick + edit profiles and bind them to the smart/aggressive buttons."""

    def __init__(
        self,
        settings: QSettings,
        on_apply: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Resource Cleanup Settings")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumWidth(440)

        self._settings = settings
        self._on_apply = on_apply
        self._loading = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        # ---- Button binding ------------------------------------------------
        binding_group = QGroupBox("Button bindings")
        binding_form = QFormLayout(binding_group)
        self._smart_combo = QComboBox()
        self._aggressive_combo = QComboBox()
        binding_form.addRow("🧠 Smart button uses:", self._smart_combo)
        binding_form.addRow("⚡ Aggressive button uses:", self._aggressive_combo)
        layout.addWidget(binding_group)

        # ---- Profile editor ------------------------------------------------
        editor_group = QGroupBox("Edit profile")
        editor_layout = QVBoxLayout(editor_group)

        picker_row = QHBoxLayout()
        picker_row.addWidget(QLabel("Profile:"))
        self._editor_combo = QComboBox()
        picker_row.addWidget(self._editor_combo, 1)
        self._reset_btn = QPushButton("Reset to default")
        self._reset_btn.setToolTip("Discard your customisation for this profile")
        picker_row.addWidget(self._reset_btn)
        editor_layout.addLayout(picker_row)

        hline = QFrame()
        hline.setObjectName("hline")
        hline.setFrameShape(QFrame.Shape.HLine)
        hline.setFixedHeight(1)
        editor_layout.addWidget(hline)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(6)

        self._pressure_threshold = QDoubleSpinBox()
        self._pressure_threshold.setRange(50.0, 99.0)
        self._pressure_threshold.setSuffix(" %")
        self._pressure_threshold.setDecimals(0)
        self._pressure_threshold.setToolTip(
            "Skip the heavy scan when system memory usage is below this percent.\n"
            "Higher = less disk I/O, but reclaim runs less often."
        )
        form.addRow("Pressure threshold:", self._pressure_threshold)

        self._enable_trim = QCheckBox("Trim working sets")
        form.addRow("", self._enable_trim)

        self._trim_threshold = QSpinBox()
        self._trim_threshold.setRange(50, 4096)
        self._trim_threshold.setSuffix(" MB")
        self._trim_threshold.setSingleStep(50)
        self._trim_threshold.setToolTip("Only trim processes whose RSS exceeds this size.")
        form.addRow("Trim if RSS ≥:", self._trim_threshold)

        self._max_trim = QSpinBox()
        self._max_trim.setRange(0, 20)
        self._max_trim.setToolTip("Hard cap on processes trimmed per run. Lower = less paging churn.")
        form.addRow("Max trims/run:", self._max_trim)

        self._trim_cooldown = QSpinBox()
        self._trim_cooldown.setRange(15, 1800)
        self._trim_cooldown.setSuffix(" s")
        self._trim_cooldown.setSingleStep(15)
        self._trim_cooldown.setToolTip("How long before the same process can be trimmed again.")
        form.addRow("Trim cooldown:", self._trim_cooldown)

        self._flush_combo = QComboBox()
        for mode in FLUSH_MODES:
            self._flush_combo.addItem(_FLUSH_LABELS[mode], mode)
        self._flush_combo.setToolTip(
            "Purges the Windows standby file cache.\n"
            "BIGGEST source of disk R/W spikes — keep on 'Never' if R/W is bothering you."
        )
        form.addRow("Standby cache flush:", self._flush_combo)

        self._enable_throttle = QCheckBox("Throttle hot processes (priority/affinity)")
        form.addRow("", self._enable_throttle)

        self._max_throttle = QSpinBox()
        self._max_throttle.setRange(0, 10)
        form.addRow("Max throttles/run:", self._max_throttle)

        self._throttle_cooldown = QSpinBox()
        self._throttle_cooldown.setRange(15, 1800)
        self._throttle_cooldown.setSuffix(" s")
        self._throttle_cooldown.setSingleStep(15)
        form.addRow("Throttle cooldown:", self._throttle_cooldown)

        self._protect_foreground = QCheckBox("Never trim/throttle the foreground app")
        form.addRow("", self._protect_foreground)

        self._grace = QSpinBox()
        self._grace.setRange(0, 600)
        self._grace.setSuffix(" s")
        self._grace.setToolTip("Don't touch processes younger than this.")
        form.addRow("New process grace:", self._grace)

        self._run_gc = QCheckBox("Run Python GC on this process before scanning")
        form.addRow("", self._run_gc)

        # --- Termination (Nuclear-tier) -------------------------------------
        self._enable_kill = QCheckBox("Terminate non-spared background processes")
        self._enable_kill.setToolTip(
            "DESTRUCTIVE — kills user-owned processes that are not spared.\n"
            "Use with caution. Unsaved data in killed apps will be lost."
        )
        form.addRow("☠ Termination:", self._enable_kill)

        self._spare_visible = QCheckBox("Spare processes with a visible window")
        form.addRow("", self._spare_visible)

        self._spare_tray = QCheckBox("Spare processes with a system tray icon")
        form.addRow("", self._spare_tray)

        self._confirm_kill = QCheckBox("Show confirmation dialog before killing")
        self._confirm_kill.setToolTip(
            "When checked, you'll see the kill list and can cancel before anything dies."
        )
        form.addRow("", self._confirm_kill)

        # --- System-wide reclaim (admin only) -------------------------------
        self._empty_ws = QCheckBox("Empty all working sets system-wide (admin only)")
        self._empty_ws.setToolTip(
            "Issues NtSetSystemInformation(MemoryEmptyWorkingSets).\n"
            "Silently no-ops without admin privilege."
        )
        form.addRow("", self._empty_ws)

        self._flush_modified = QCheckBox("Flush modified page list to disk")
        self._flush_modified.setToolTip(
            "Forces dirty pages to be written, freeing them for reclaim."
        )
        form.addRow("", self._flush_modified)

        editor_layout.addLayout(form)
        layout.addWidget(editor_group, 1)

        # ---- Buttons -------------------------------------------------------
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close
        )
        save_btn = button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("Save && Apply")
        button_box.accepted.connect(self._on_save)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._populate_profiles()
        self._connect_signals()

    # ------------------------------------------------------------------
    # Population & sync
    # ------------------------------------------------------------------
    def _populate_profiles(self) -> None:
        self._loading = True
        try:
            profiles = all_profiles(self._settings)
            names = [p.name for p in profiles]

            for combo in (self._smart_combo, self._aggressive_combo, self._editor_combo):
                combo.blockSignals(True)
                combo.clear()
                combo.addItems(names)
                combo.blockSignals(False)

            smart = load_active_smart_profile(self._settings).name
            aggressive = load_active_aggressive_profile(self._settings).name
            self._select_in_combo(self._smart_combo, smart)
            self._select_in_combo(self._aggressive_combo, aggressive)
            self._select_in_combo(self._editor_combo, smart)
            self._load_profile_into_editor(smart)
        finally:
            self._loading = False

    def _connect_signals(self) -> None:
        self._editor_combo.currentTextChanged.connect(self._on_editor_changed)
        self._reset_btn.clicked.connect(self._on_reset)

    def _select_in_combo(self, combo: QComboBox, name: str) -> None:
        index = combo.findText(name)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _load_profile_into_editor(self, name: str) -> None:
        profile = load_profile(self._settings, name)
        self._loading = True
        try:
            self._pressure_threshold.setValue(float(profile.pressure_threshold_percent))
            self._enable_trim.setChecked(profile.enable_trim)
            self._trim_threshold.setValue(int(profile.trim_threshold_mb))
            self._max_trim.setValue(int(profile.max_trim_per_run))
            self._trim_cooldown.setValue(int(profile.trim_cooldown_seconds))
            flush_index = self._flush_combo.findData(profile.flush_standby)
            self._flush_combo.setCurrentIndex(flush_index if flush_index >= 0 else 1)
            self._enable_throttle.setChecked(profile.enable_throttle)
            self._max_throttle.setValue(int(profile.max_throttle_per_run))
            self._throttle_cooldown.setValue(int(profile.throttle_cooldown_seconds))
            self._protect_foreground.setChecked(profile.protect_foreground)
            self._grace.setValue(int(profile.new_process_grace_seconds))
            self._run_gc.setChecked(profile.run_gc)
            self._enable_kill.setChecked(profile.enable_kill)
            self._spare_visible.setChecked(profile.spare_visible_windows)
            self._spare_tray.setChecked(profile.spare_tray_icons)
            self._confirm_kill.setChecked(profile.confirm_before_kill)
            self._empty_ws.setChecked(profile.empty_all_working_sets)
            self._flush_modified.setChecked(profile.flush_modified_pages)
        finally:
            self._loading = False

    def _editor_to_profile(self) -> ResourceProfile:
        name = self._editor_combo.currentText() or "Balanced"
        base = load_profile(self._settings, name)
        flush_value = self._flush_combo.currentData() or FLUSH_CRITICAL_ONLY
        return base.with_overrides(
            pressure_threshold_percent=float(self._pressure_threshold.value()),
            enable_trim=self._enable_trim.isChecked(),
            trim_threshold_mb=float(self._trim_threshold.value()),
            max_trim_per_run=int(self._max_trim.value()),
            trim_cooldown_seconds=float(self._trim_cooldown.value()),
            flush_standby=str(flush_value),
            enable_throttle=self._enable_throttle.isChecked(),
            max_throttle_per_run=int(self._max_throttle.value()),
            throttle_cooldown_seconds=float(self._throttle_cooldown.value()),
            protect_foreground=self._protect_foreground.isChecked(),
            new_process_grace_seconds=float(self._grace.value()),
            run_gc=self._run_gc.isChecked(),
            enable_kill=self._enable_kill.isChecked(),
            spare_visible_windows=self._spare_visible.isChecked(),
            spare_tray_icons=self._spare_tray.isChecked(),
            confirm_before_kill=self._confirm_kill.isChecked(),
            empty_all_working_sets=self._empty_ws.isChecked(),
            flush_modified_pages=self._flush_modified.isChecked(),
        )

    # ------------------------------------------------------------------
    # Slot handlers
    # ------------------------------------------------------------------
    def _on_editor_changed(self, name: str) -> None:
        if self._loading or not name:
            return
        self._load_profile_into_editor(name)

    def _on_reset(self) -> None:
        name = self._editor_combo.currentText()
        if not name:
            return
        if get_preset(name) is None:
            return
        reset_custom_profile(self._settings, name)
        self._load_profile_into_editor(name)
        LOGGER.info("Reset resource profile '%s' to defaults", name)

    def _on_save(self) -> None:
        try:
            edited = self._editor_to_profile()
            if get_preset(edited.name) is None:
                # Future-proof: editing a non-builtin (e.g. user-defined) — skip if no fields differ.
                pass
            base_preset = get_preset(edited.name)
            if base_preset is not None and not _profile_differs(edited, base_preset):
                # The user reverted everything — drop the customisation.
                reset_custom_profile(self._settings, edited.name)
            else:
                save_custom_profile(self._settings, edited)

            set_active_smart_profile(self._settings, self._smart_combo.currentText())
            set_active_aggressive_profile(self._settings, self._aggressive_combo.currentText())

            if self._on_apply is not None:
                self._on_apply()
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Failed to save resource settings")
        self.accept()


def _profile_differs(a: ResourceProfile, b: ResourceProfile) -> bool:
    for f in fields(a):
        if f.name == "name":
            continue
        if getattr(a, f.name) != getattr(b, f.name):
            return True
    return False


def open_resource_settings_dialog(
    settings: QSettings,
    on_apply: Callable[[], None] | None = None,
    parent: QWidget | None = None,
) -> None:
    dialog = ResourceSettingsDialog(settings, on_apply=on_apply, parent=parent)
    dialog.exec()

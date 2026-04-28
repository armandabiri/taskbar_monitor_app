"""Shared-mode microphone recorder that writes MP3 files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any

from PyQt6.QtCore import QSettings

from core.config import recordings_dir as default_recordings_dir

try:
    import lameenc as _lameenc
except ImportError:  # pragma: no cover - exercised via runtime dependency checks
    _lameenc = None

try:
    import sounddevice as _sounddevice
except ImportError:  # pragma: no cover - exercised via runtime dependency checks
    _sounddevice = None

LOGGER = logging.getLogger(__name__)

SETTINGS_GROUP = "recording"
KEY_OUTPUT_DIR = "output_dir"
KEY_FILENAME_PREFIX = "filename_prefix"
KEY_BITRATE_KBPS = "bitrate_kbps"
KEY_SAMPLE_RATE_HZ = "sample_rate_hz"
KEY_CHANNELS = "channels"
KEY_OPEN_FOLDER_AFTER_SAVE = "open_folder_after_save"

DEFAULT_MP3_BITRATE_KBPS = 128
DEFAULT_SAMPLE_RATE_HZ = 44100
DEFAULT_BLOCK_FRAMES = 4096
STARTUP_TIMEOUT_SECONDS = 5.0
DEVICE_DEFAULT_SAMPLE_RATE = 0
DEFAULT_FILENAME_PREFIX = "mic_recording"
DEFAULT_RECORDING_CHANNELS = 1
MIN_BITRATE_KBPS = 64
MAX_BITRATE_KBPS = 320
SUPPORTED_SAMPLE_RATES = (DEVICE_DEFAULT_SAMPLE_RATE, 22050, 32000, 44100, 48000)
SUPPORTED_CHANNELS = (1, 2)


@dataclass(frozen=True)
class RecordingSettings:
    """Persistent microphone-recording settings."""

    output_dir: str = ""
    filename_prefix: str = DEFAULT_FILENAME_PREFIX
    bitrate_kbps: int = DEFAULT_MP3_BITRATE_KBPS
    sample_rate_hz: int = DEVICE_DEFAULT_SAMPLE_RATE
    channels: int = DEFAULT_RECORDING_CHANNELS
    open_folder_after_save: bool = False

    def normalized(self) -> "RecordingSettings":
        """Return a sanitized copy safe for runtime use."""
        prefix = _sanitize_prefix(self.filename_prefix)
        bitrate = min(max(int(self.bitrate_kbps), MIN_BITRATE_KBPS), MAX_BITRATE_KBPS)
        sample_rate = (
            int(self.sample_rate_hz)
            if int(self.sample_rate_hz) in SUPPORTED_SAMPLE_RATES
            else DEVICE_DEFAULT_SAMPLE_RATE
        )
        channels = 2 if int(self.channels) >= 2 else 1
        output_dir = str(self.output_dir or "").strip()
        return RecordingSettings(
            output_dir=output_dir,
            filename_prefix=prefix,
            bitrate_kbps=bitrate,
            sample_rate_hz=sample_rate,
            channels=channels,
            open_folder_after_save=bool(self.open_folder_after_save),
        )

    def effective_output_dir(self) -> str:
        """Return the active recording directory, creating it if needed."""
        target = self.output_dir.strip() or default_recordings_dir()
        os.makedirs(target, exist_ok=True)
        return target


def load_recording_settings(settings: QSettings) -> RecordingSettings:
    """Load persisted recording settings from QSettings."""
    base = RecordingSettings()
    loaded = RecordingSettings(
        output_dir=_read_setting_str(settings, f"{SETTINGS_GROUP}/{KEY_OUTPUT_DIR}", base.output_dir),
        filename_prefix=_read_setting_str(
            settings, f"{SETTINGS_GROUP}/{KEY_FILENAME_PREFIX}", base.filename_prefix
        ),
        bitrate_kbps=_read_setting_int(
            settings, f"{SETTINGS_GROUP}/{KEY_BITRATE_KBPS}", base.bitrate_kbps
        ),
        sample_rate_hz=_read_setting_int(
            settings, f"{SETTINGS_GROUP}/{KEY_SAMPLE_RATE_HZ}", base.sample_rate_hz
        ),
        channels=_read_setting_int(settings, f"{SETTINGS_GROUP}/{KEY_CHANNELS}", base.channels),
        open_folder_after_save=_read_setting_bool(
            settings,
            f"{SETTINGS_GROUP}/{KEY_OPEN_FOLDER_AFTER_SAVE}",
            base.open_folder_after_save,
        ),
    )
    return loaded.normalized()


def save_recording_settings(settings: QSettings, recording: RecordingSettings) -> None:
    """Persist recording settings to QSettings."""
    normalized = recording.normalized()
    settings.setValue(f"{SETTINGS_GROUP}/{KEY_OUTPUT_DIR}", normalized.output_dir)
    settings.setValue(f"{SETTINGS_GROUP}/{KEY_FILENAME_PREFIX}", normalized.filename_prefix)
    settings.setValue(f"{SETTINGS_GROUP}/{KEY_BITRATE_KBPS}", normalized.bitrate_kbps)
    settings.setValue(f"{SETTINGS_GROUP}/{KEY_SAMPLE_RATE_HZ}", normalized.sample_rate_hz)
    settings.setValue(f"{SETTINGS_GROUP}/{KEY_CHANNELS}", normalized.channels)
    settings.setValue(
        f"{SETTINGS_GROUP}/{KEY_OPEN_FOLDER_AFTER_SAVE}",
        1 if normalized.open_folder_after_save else 0,
    )
    settings.sync()


class MicrophoneRecorderError(RuntimeError):
    """Raised when microphone recording cannot be started or stopped safely."""


@dataclass(frozen=True)
class RecordingSessionInfo:
    """Metadata for one microphone recording session."""

    output_path: str
    device_name: str
    samplerate_hz: int
    channels: int
    bitrate_kbps: int
    started_at: float


class MicrophoneRecorder:
    """Record the system default microphone to an MP3 file."""

    def __init__(self, settings: RecordingSettings | None = None) -> None:
        self._lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stop_event: threading.Event | None = None
        self._active_session: RecordingSessionInfo | None = None
        self._last_completed_session: RecordingSessionInfo | None = None
        self._last_error: str | None = None
        self._settings = (settings or RecordingSettings()).normalized()

    @property
    def is_recording(self) -> bool:
        """Whether a microphone recording is currently active."""
        with self._lock:
            return self._active_session is not None

    @property
    def active_session(self) -> RecordingSessionInfo | None:
        """Return the active recording session, if any."""
        with self._lock:
            return self._active_session

    @property
    def last_completed_session(self) -> RecordingSessionInfo | None:
        """Return the most recently completed recording session, if any."""
        with self._lock:
            return self._last_completed_session

    @property
    def settings(self) -> RecordingSettings:
        """Return the current recorder settings."""
        with self._lock:
            return self._settings

    def update_settings(self, settings: RecordingSettings) -> None:
        """Apply new settings for subsequent recordings."""
        with self._lock:
            self._settings = settings.normalized()

    def start_recording(self) -> RecordingSessionInfo:
        """Start a new microphone recording and return its session metadata."""
        sounddevice = self._require_sounddevice()
        self._require_lameenc()

        with self._lock:
            if self._active_session is not None:
                raise MicrophoneRecorderError("A microphone recording is already in progress.")
            self._last_error = None
            active_settings = self._settings

        device_info = self._resolve_input_device(sounddevice)
        channels = self._resolve_channels(device_info, active_settings)
        try:
            output_path = self._build_output_path(active_settings)
        except OSError as exc:
            target_dir = active_settings.output_dir.strip() or default_recordings_dir()
            raise MicrophoneRecorderError(
                f"Recording folder is not writable: {target_dir}"
            ) from exc
        session = RecordingSessionInfo(
            output_path=output_path,
            device_name=str(device_info.get("name") or "Microphone"),
            samplerate_hz=self._resolve_sample_rate(device_info, active_settings),
            channels=channels,
            bitrate_kbps=active_settings.bitrate_kbps,
            started_at=time.time(),
        )
        startup_event = threading.Event()
        startup_error: list[str] = []
        stop_event = threading.Event()
        worker = threading.Thread(
            target=self._record_worker,
            name="MicrophoneRecorder",
            args=(session, device_info, stop_event, startup_event, startup_error),
            daemon=True,
        )

        with self._lock:
            self._active_session = session
            self._stop_event = stop_event
            self._worker = worker

        worker.start()
        if not startup_event.wait(timeout=STARTUP_TIMEOUT_SECONDS):
            self._request_stop()
            worker.join(timeout=1.0)
            raise MicrophoneRecorderError("Microphone recording timed out while starting.")
        if startup_error:
            worker.join(timeout=1.0)
            raise MicrophoneRecorderError(startup_error[0])
        return session

    def stop_recording(self) -> RecordingSessionInfo:
        """Stop the active recording, flush the MP3 file, and return its metadata."""
        with self._lock:
            session = self._active_session
            worker = self._worker
            stop_event = self._stop_event
        if session is None or worker is None or stop_event is None:
            raise MicrophoneRecorderError("No microphone recording is currently active.")

        stop_event.set()
        worker.join(timeout=5.0)
        if worker.is_alive():
            raise MicrophoneRecorderError("Microphone recording did not stop cleanly.")

        error = self.consume_last_error()
        if error is not None:
            raise MicrophoneRecorderError(error)
        return session

    def consume_last_error(self) -> str | None:
        """Return and clear the most recent recorder error."""
        with self._lock:
            error = self._last_error
            self._last_error = None
            return error

    def _record_worker(
        self,
        session: RecordingSessionInfo,
        device_info: dict[str, Any],
        stop_event: threading.Event,
        startup_event: threading.Event,
        startup_error: list[str],
    ) -> None:
        output_size = 0
        overflow_count = 0
        failed = False
        sounddevice = self._require_sounddevice()
        lameenc = self._require_lameenc()
        extra_settings = self._build_extra_settings(sounddevice, device_info)

        try:
            encoder = self._build_encoder(lameenc, session)
            with Path(session.output_path).open("wb") as output_file:
                with sounddevice.RawInputStream(
                    samplerate=session.samplerate_hz,
                    blocksize=DEFAULT_BLOCK_FRAMES,
                    device=device_info.get("index"),
                    channels=session.channels,
                    dtype="int16",
                    latency="high",
                    extra_settings=extra_settings,
                ) as stream:
                    startup_event.set()
                    while not stop_event.is_set():
                        chunk, overflowed = stream.read(DEFAULT_BLOCK_FRAMES)
                        if overflowed:
                            overflow_count += 1
                        encoded = encoder.encode(bytes(chunk))
                        if encoded:
                            written = output_file.write(encoded)
                            output_size += written
                    encoded = encoder.flush()
                    if encoded:
                        written = output_file.write(encoded)
                        output_size += written
        except Exception as exc:  # pylint: disable=broad-exception-caught
            failed = True
            message = f"Microphone recording failed: {exc}"
            LOGGER.exception("Microphone recording failed")
            if not startup_event.is_set():
                startup_error.append(message)
                startup_event.set()
            self._store_error(message)
        finally:
            if overflow_count:
                LOGGER.warning("Microphone recording overflowed %s time(s)", overflow_count)
            if not failed and output_size > 0:
                with self._lock:
                    self._last_completed_session = session
            elif failed or output_size <= 0:
                self._delete_partial_output(session.output_path)
            self._clear_active_session(session)

    def _resolve_input_device(self, sounddevice) -> dict[str, Any]:
        try:
            device = sounddevice.query_devices(kind="input")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            raise MicrophoneRecorderError("No usable microphone input device is available.") from exc
        max_input_channels = int(device.get("max_input_channels") or 0)
        if max_input_channels < 1:
            raise MicrophoneRecorderError("The default input device does not expose a microphone channel.")
        return dict(device)

    def _build_output_path(self, settings: RecordingSettings) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")
        filename = f"{settings.filename_prefix}_{timestamp}.mp3"
        return str(Path(settings.effective_output_dir()) / filename)

    def _resolve_sample_rate(self, device_info: dict[str, Any], settings: RecordingSettings) -> int:
        if settings.sample_rate_hz > 0:
            return int(settings.sample_rate_hz)
        raw_value = device_info.get("default_samplerate")
        try:
            sample_rate = int(float(raw_value))
        except (TypeError, ValueError):
            sample_rate = DEFAULT_SAMPLE_RATE_HZ
        return sample_rate if sample_rate > 0 else DEFAULT_SAMPLE_RATE_HZ

    def _resolve_channels(self, device_info: dict[str, Any], settings: RecordingSettings) -> int:
        max_input_channels = int(device_info.get("max_input_channels") or 1)
        requested = 2 if settings.channels >= 2 else 1
        return max(1, min(max_input_channels, requested))

    def _build_extra_settings(self, sounddevice, device_info: dict[str, Any]):
        hostapi_index = device_info.get("hostapi")
        if hostapi_index is None or not hasattr(sounddevice, "WasapiSettings"):
            return None
        try:
            hostapi = sounddevice.query_hostapis(int(hostapi_index))
        except Exception:  # pylint: disable=broad-exception-caught
            return None
        hostapi_name = str(hostapi.get("name") or "").lower()
        if "wasapi" not in hostapi_name:
            return None
        # Explicitly force shared mode so the recorder does not try to take
        # exclusive ownership of the microphone device.
        return sounddevice.WasapiSettings(exclusive=False, auto_convert=True)

    def _build_encoder(self, lameenc, session: RecordingSessionInfo):
        encoder = lameenc.Encoder()
        encoder.set_bit_rate(session.bitrate_kbps)
        encoder.set_in_sample_rate(session.samplerate_hz)
        encoder.set_channels(session.channels)
        encoder.set_quality(2)
        return encoder

    def _request_stop(self) -> None:
        with self._lock:
            stop_event = self._stop_event
        if stop_event is not None:
            stop_event.set()

    def _clear_active_session(self, session: RecordingSessionInfo) -> None:
        with self._lock:
            if self._active_session == session:
                self._active_session = None
                self._stop_event = None
                self._worker = None

    def _store_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message

    def _delete_partial_output(self, output_path: str) -> None:
        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except OSError:
            LOGGER.warning("Failed to remove partial microphone recording: %s", output_path)

    def _require_sounddevice(self):
        if _sounddevice is None:
            raise MicrophoneRecorderError(
                "Microphone recording dependencies are missing: install 'sounddevice'."
            )
        return _sounddevice

    def _require_lameenc(self):
        if _lameenc is None:
            raise MicrophoneRecorderError(
                "Microphone recording dependencies are missing: install 'lameenc'."
            )
        return _lameenc


def _sanitize_prefix(prefix: str) -> str:
    cleaned = "".join(
        char if char.isalnum() or char in {"-", "_"} else "_"
        for char in str(prefix or "").strip()
    ).strip("._ ")
    return cleaned or DEFAULT_FILENAME_PREFIX


def _read_setting_str(settings: QSettings, key: str, default: str) -> str:
    value = settings.value(key, default)
    return default if value is None else str(value)


def _read_setting_int(settings: QSettings, key: str, default: int) -> int:
    value = settings.value(key, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _read_setting_bool(settings: QSettings, key: str, default: bool) -> bool:
    value = settings.value(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}

from __future__ import annotations

from pathlib import Path
import time

import pytest
from PyQt6.QtCore import QSettings

from services import microphone_recorder as recorder_module
from services.microphone_recorder import (
    MicrophoneRecorder,
    MicrophoneRecorderError,
    RecordingSettings,
    load_recording_settings,
    save_recording_settings,
)


class _FakeEncoder:
    last_instance = None

    def __init__(self) -> None:
        type(self).last_instance = self

    def set_bit_rate(self, value) -> None:
        self.bit_rate = value

    def set_in_sample_rate(self, value) -> None:
        self.sample_rate = value

    def set_channels(self, value) -> None:
        self.channels = value

    def set_quality(self, value) -> None:
        self.quality = value

    def encode(self, chunk: bytes) -> bytes:
        return b"MP3" + chunk[:8]

    def flush(self) -> bytes:
        return b"END"


class _FakeLameenc:
    Encoder = _FakeEncoder


class _FakeWasapiSettings:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs


class _FakeRawInputStream:
    last_kwargs = None

    def __init__(self, **kwargs) -> None:
        _FakeRawInputStream.last_kwargs = kwargs

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def read(self, frames: int):
        time.sleep(0.01)
        return (b"\x01\x00" * frames, False)


class _FakeSoundDevice:
    RawInputStream = _FakeRawInputStream
    WasapiSettings = _FakeWasapiSettings

    @staticmethod
    def query_devices(*, kind: str):
        assert kind == "input"
        return {
            "name": "Fake Mic",
            "index": 2,
            "hostapi": 7,
            "max_input_channels": 1,
            "default_samplerate": 48000.0,
        }

    @staticmethod
    def query_hostapis(index: int):
        assert index == 7
        return {"name": "Windows WASAPI"}


class _BrokenRawInputStream:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    def __enter__(self):
        raise RuntimeError("open failed")

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _BrokenSoundDevice(_FakeSoundDevice):
    RawInputStream = _BrokenRawInputStream


def test_microphone_recorder_writes_mp3_and_uses_shared_wasapi(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(recorder_module, "_sounddevice", _FakeSoundDevice)
    monkeypatch.setattr(recorder_module, "_lameenc", _FakeLameenc)
    monkeypatch.setattr(recorder_module, "default_recordings_dir", lambda: str(tmp_path))

    recorder = MicrophoneRecorder()
    session = recorder.start_recording()
    assert recorder.is_recording

    stopped = recorder.stop_recording()
    assert stopped.output_path == session.output_path
    assert recorder.is_recording is False

    output = Path(session.output_path)
    assert output.exists()
    assert output.read_bytes().startswith(b"MP3")
    assert output.parent == tmp_path
    assert output.name.startswith("mic_recording_")
    extra_settings = _FakeRawInputStream.last_kwargs["extra_settings"]
    assert isinstance(extra_settings, _FakeWasapiSettings)
    assert extra_settings.kwargs["exclusive"] is False
    assert extra_settings.kwargs["auto_convert"] is True
    assert _FakeEncoder.last_instance.bit_rate == 128


def test_microphone_recorder_cleans_up_partial_file_on_start_failure(
    monkeypatch, tmp_path: Path,
) -> None:
    monkeypatch.setattr(recorder_module, "_sounddevice", _BrokenSoundDevice)
    monkeypatch.setattr(recorder_module, "_lameenc", _FakeLameenc)
    monkeypatch.setattr(recorder_module, "default_recordings_dir", lambda: str(tmp_path))

    recorder = MicrophoneRecorder()
    with pytest.raises(MicrophoneRecorderError, match="Microphone recording failed"):
        recorder.start_recording()

    assert list(tmp_path.iterdir()) == []


def test_microphone_recorder_honors_custom_recording_settings(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(recorder_module, "_sounddevice", _FakeSoundDevice)
    monkeypatch.setattr(recorder_module, "_lameenc", _FakeLameenc)
    monkeypatch.setattr(recorder_module, "default_recordings_dir", lambda: str(tmp_path / "default"))

    custom_dir = tmp_path / "custom-recordings"
    recorder = MicrophoneRecorder(
        RecordingSettings(
            output_dir=str(custom_dir),
            filename_prefix="meeting notes",
            bitrate_kbps=192,
            sample_rate_hz=32000,
            channels=2,
            open_folder_after_save=True,
        )
    )
    session = recorder.start_recording()
    recorder.stop_recording()

    assert Path(session.output_path).parent == custom_dir
    assert Path(session.output_path).name.startswith("meeting_notes_")
    assert session.samplerate_hz == 32000
    assert session.channels == 1
    assert session.bitrate_kbps == 192
    assert _FakeRawInputStream.last_kwargs["samplerate"] == 32000
    assert _FakeRawInputStream.last_kwargs["channels"] == 1
    assert _FakeEncoder.last_instance.bit_rate == 192


def test_recording_settings_roundtrip(tmp_path: Path) -> None:
    settings = QSettings(str(tmp_path / "recording.ini"), QSettings.Format.IniFormat)
    recording = RecordingSettings(
        output_dir=str(tmp_path / "clips"),
        filename_prefix="podcast",
        bitrate_kbps=160,
        sample_rate_hz=44100,
        channels=2,
        open_folder_after_save=True,
    )
    save_recording_settings(settings, recording)

    loaded = load_recording_settings(settings)
    assert loaded == recording.normalized()

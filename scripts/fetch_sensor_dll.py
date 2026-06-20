"""Download, verify, and pin the embedded LibreHardwareMonitorLib sensor DLL.

The Taskbar Monitor reads CPU/RAM/GPU/SSD temperatures in-process through
``LibreHardwareMonitorLib.dll`` (MPL-2.0, redistributable). This script fetches a
pinned release of that DLL into ``src/assets/sensors/`` so PyInstaller can bundle
it, and verifies the bundled copy against a pinned SHA-256.

Usage::

    python scripts/fetch_sensor_dll.py --download
    python scripts/fetch_sensor_dll.py --verify

Exit codes:
    0  success (downloaded, or verify passed)
    1  the DLL is missing on --verify
    2  the DLL checksum does not match the pin on --verify
    3  a download or extraction error
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import urllib.request
import zipfile
from pathlib import Path

DLL_NAME = "LibreHardwareMonitorLib.dll"
TARGET_DIR = Path(__file__).resolve().parents[1] / "src" / "assets" / "sensors"
TARGET_PATH = TARGET_DIR / DLL_NAME

# Pinned LibreHardwareMonitor release. The release ships a zip; the DLL lives at
# its top level. Update PINNED_URL and EXPECTED_SHA256 together when bumping.
PINNED_URL = (
    "https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/"
    "download/v0.9.4/LibreHardwareMonitor-net472.zip"
)
# SHA-256 of the extracted LibreHardwareMonitorLib.dll. Empty string means the
# pin is unset: --verify then checks presence only and prints a warning so the
# maintainer fills it after the first trusted download.
EXPECTED_SHA256 = "a0f2728f1734c236a9d02d9e25a88bc4f8cb7bd1faff1770726beb7af06bf8dc"

_HTTP_TIMEOUT = 30.0


def _sha256(data: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(data)
    return digest.hexdigest()


def _extract_dll_from_zip(raw: bytes) -> bytes | None:
    """Return the DLL bytes from a release zip, or None when not found."""
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        for entry in archive.namelist():
            if Path(entry).name.lower() == DLL_NAME.lower():
                return archive.read(entry)
    return None


def download() -> int:
    """Fetch the pinned DLL into TARGET_DIR. Return a process exit code."""
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    try:
        request = urllib.request.Request(PINNED_URL, method="GET")
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as resp:
            raw = resp.read()
    except OSError as exc:
        print(f"sensor dll download failed: {exc}")
        return 3

    payload = raw
    if PINNED_URL.lower().endswith(".zip"):
        payload = _extract_dll_from_zip(raw)
        if payload is None:
            print("sensor dll not found inside the release archive")
            return 3

    TARGET_PATH.write_bytes(payload)
    print(f"sensor dll written to {TARGET_PATH} ({len(payload)} bytes, sha256={_sha256(payload)})")
    if EXPECTED_SHA256 and _sha256(payload) != EXPECTED_SHA256:
        print("sensor dll checksum mismatch")
        return 2
    return 0


def verify() -> int:
    """Verify the bundled DLL exists and matches the pin. Return an exit code."""
    if not TARGET_PATH.exists():
        print("sensor dll missing")
        return 1
    actual = _sha256(TARGET_PATH.read_bytes())
    if not EXPECTED_SHA256:
        print(f"sensor dll present, pin unset (sha256={actual})")
        return 0
    if actual != EXPECTED_SHA256:
        print("sensor dll checksum mismatch")
        return 2
    print("sensor dll verified")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch or verify the sensor DLL.")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--download", action="store_true", help="download the pinned DLL")
    group.add_argument("--verify", action="store_true", help="verify the bundled DLL")
    args = parser.parse_args(argv)
    if args.download:
        return download()
    return verify()


if __name__ == "__main__":
    sys.exit(main())

"""Process snapshots — capture every running process to a CSV.

Snapshots are used for two things:
  1. A historical record the user can browse / diff later.
  2. A 'spare set' for the smart-kill cleanup mode: processes whose
     (name, exe) pair is in the snapshot are spared, so cleanup only
     terminates apps that have appeared *since* the snapshot was taken.

PIDs are not stable across reboots; matching is by (name_lower, exe_lower).
"""

from __future__ import annotations

import csv
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Iterable

import psutil
from PyQt6.QtCore import QStandardPaths

LOGGER = logging.getLogger(__name__)

_FILENAME_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1F]')
_SNAPSHOT_SUFFIX = ".csv"
_FIELDS = (
    "pid", "name", "exe", "username", "cpu_percent",
    "rss_mb", "uss_mb", "create_time", "status", "num_threads", "cmdline",
)


@dataclass
class ProcessSnapshotEntry:
    pid: int
    name: str
    exe: str
    username: str
    cpu_percent: float
    rss_mb: float
    uss_mb: float | None
    create_time: float
    status: str
    num_threads: int
    cmdline: str


@dataclass
class ProcessSnapshot:
    """A captured set of processes, named by the user."""
    name: str
    taken_at: float
    path: str
    entries: list[ProcessSnapshotEntry] = field(default_factory=list)

    @property
    def process_count(self) -> int:
        return len(self.entries)

    @property
    def total_rss_gb(self) -> float:
        return sum(e.rss_mb for e in self.entries) / 1024.0

    def spare_keys(self) -> set[tuple[str, str]]:
        """Return (name_lower, exe_lower) tuples used for matching live processes."""
        return {(e.name.lower(), e.exe.lower()) for e in self.entries}


def snapshots_dir() -> str:
    """Directory where snapshot CSVs are stored. Created on first call."""
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    if not base:
        base = os.path.expanduser("~/.taskbar-monitor")
    target = os.path.join(base, "snapshots")
    os.makedirs(target, exist_ok=True)
    return target


def sanitize_name(name: str) -> str:
    """Make a snapshot name safe for use as a filename stem."""
    cleaned = _FILENAME_FORBIDDEN.sub("_", name).strip().rstrip(".")
    return cleaned or "snapshot"


CPU_SAMPLE_INTERVAL_SECONDS = 0.5


def take_snapshot(name: str | None = None) -> ProcessSnapshot:
    """Walk every process and capture its current resource consumption.

    psutil's per-process ``cpu_percent()`` returns 0 on the first call (it
    needs a prior sample as baseline). To produce a meaningful CPU% in the
    snapshot we prime every process, sleep a beat, then re-sample. Adds
    ~500ms to the snapshot but is essential for the diff viewer.
    """
    if not name:
        name = time.strftime("%Y-%m-%d_%H-%M-%S")
    name = name.strip()
    taken_at = time.time()

    # Pass 1: collect process handles + prime cpu_percent baselines.
    procs: list[psutil.Process] = []
    for proc in psutil.process_iter():
        try:
            proc.cpu_percent(interval=None)  # prime — return value is meaningless here
            procs.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    time.sleep(CPU_SAMPLE_INTERVAL_SECONDS)

    # Pass 2: snapshot the world.
    entries: list[ProcessSnapshotEntry] = []
    for proc in procs:
        try:
            with proc.oneshot():
                pid = proc.pid
                proc_name = proc.name() or ""
                try:
                    exe = proc.exe() or ""
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    exe = ""
                try:
                    username = proc.username() or ""
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    username = ""
                cpu = float(proc.cpu_percent(interval=None))
                mem = proc.memory_info()
                rss_mb = float(mem.rss) / (1024 * 1024)
                uss_mb: float | None = None
                try:
                    full = proc.memory_full_info()
                    uss_mb = float(getattr(full, "uss", 0)) / (1024 * 1024)
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    pass
                create_time = float(proc.create_time())
                status = proc.status() or ""
                num_threads = int(proc.num_threads() or 0)
                try:
                    cmdline_raw = proc.cmdline() or []
                except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
                    cmdline_raw = []
                cmdline = " ".join(cmdline_raw) if isinstance(cmdline_raw, list) else str(cmdline_raw)
            entries.append(ProcessSnapshotEntry(
                pid=int(pid),
                name=proc_name,
                exe=exe,
                username=username,
                cpu_percent=cpu,
                rss_mb=rss_mb,
                uss_mb=uss_mb,
                create_time=create_time,
                status=status,
                num_threads=num_threads,
                cmdline=cmdline,
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOGGER.debug("Skipping pid during snapshot: %s", exc)

    snapshot = ProcessSnapshot(name=name, taken_at=taken_at, path="", entries=entries)
    snapshot.path = save_snapshot(snapshot)
    return snapshot


def save_snapshot(snapshot: ProcessSnapshot) -> str:
    """Write the snapshot to its CSV file, returning the absolute path."""
    target = os.path.join(snapshots_dir(), sanitize_name(snapshot.name) + _SNAPSHOT_SUFFIX)
    with open(target, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_FIELDS)
        writer.writeheader()
        for entry in snapshot.entries:
            writer.writerow({
                "pid": entry.pid,
                "name": entry.name,
                "exe": entry.exe,
                "username": entry.username,
                "cpu_percent": f"{entry.cpu_percent:.4f}",
                "rss_mb": f"{entry.rss_mb:.3f}",
                "uss_mb": "" if entry.uss_mb is None else f"{entry.uss_mb:.3f}",
                "create_time": f"{entry.create_time:.3f}",
                "status": entry.status,
                "num_threads": entry.num_threads,
                "cmdline": entry.cmdline,
            })
    # Stamp file mtime so list_snapshots can use it as taken_at.
    os.utime(target, (snapshot.taken_at, snapshot.taken_at))
    snapshot.path = target
    return target


def load_snapshot(path: str) -> ProcessSnapshot:
    """Read a snapshot CSV from disk."""
    name = os.path.splitext(os.path.basename(path))[0]
    taken_at = os.path.getmtime(path)
    entries: list[ProcessSnapshotEntry] = []
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            try:
                entries.append(ProcessSnapshotEntry(
                    pid=int(row.get("pid") or 0),
                    name=row.get("name") or "",
                    exe=row.get("exe") or "",
                    username=row.get("username") or "",
                    cpu_percent=float(row.get("cpu_percent") or 0.0),
                    rss_mb=float(row.get("rss_mb") or 0.0),
                    uss_mb=float(row["uss_mb"]) if row.get("uss_mb") else None,
                    create_time=float(row.get("create_time") or 0.0),
                    status=row.get("status") or "",
                    num_threads=int(row.get("num_threads") or 0),
                    cmdline=row.get("cmdline") or "",
                ))
            except (TypeError, ValueError) as exc:
                LOGGER.warning("Bad snapshot row in %s: %s", path, exc)
    return ProcessSnapshot(name=name, taken_at=taken_at, path=path, entries=entries)


def list_snapshots() -> list[ProcessSnapshot]:
    """Return all stored snapshots, newest first. Entries are NOT loaded —
    use load_snapshot(path) to populate them when needed."""
    target = snapshots_dir()
    found: list[ProcessSnapshot] = []
    try:
        for entry in os.listdir(target):
            if not entry.endswith(_SNAPSHOT_SUFFIX):
                continue
            path = os.path.join(target, entry)
            try:
                taken_at = os.path.getmtime(path)
            except OSError:
                continue
            found.append(ProcessSnapshot(
                name=os.path.splitext(entry)[0],
                taken_at=taken_at,
                path=path,
            ))
    except OSError as exc:
        LOGGER.warning("snapshots_dir listing failed: %s", exc)
    found.sort(key=lambda s: s.taken_at, reverse=True)
    return found


def rename_snapshot(snapshot: ProcessSnapshot, new_name: str) -> ProcessSnapshot:
    """Rename a snapshot (renames the underlying CSV)."""
    new_name = (new_name or "").strip()
    if not new_name:
        raise ValueError("New name must not be empty")
    new_path = os.path.join(snapshots_dir(), sanitize_name(new_name) + _SNAPSHOT_SUFFIX)
    if os.path.abspath(new_path) == os.path.abspath(snapshot.path):
        return snapshot  # no-op
    if os.path.exists(new_path):
        raise FileExistsError(f"A snapshot named '{new_name}' already exists")
    os.rename(snapshot.path, new_path)
    snapshot.name = os.path.splitext(os.path.basename(new_path))[0]
    snapshot.path = new_path
    return snapshot


def delete_snapshot(snapshot: ProcessSnapshot) -> None:
    """Remove a snapshot file from disk."""
    try:
        os.remove(snapshot.path)
    except FileNotFoundError:
        pass


@dataclass
class AggregateRow:
    """Per (name, exe) totals across all instances in a snapshot."""
    key: tuple[str, str]
    name: str
    exe: str
    instances: int = 0
    cpu_percent_total: float = 0.0
    rss_mb_total: float = 0.0


@dataclass
class DiffEntry:
    """A single row in a snapshot-vs-snapshot diff.

    ``status`` is one of ``'added'`` (key only in 'new'), ``'removed'``
    (key only in 'old'), or ``'changed'`` (key in both).
    """
    key: tuple[str, str]
    name: str
    exe: str
    status: str
    old: AggregateRow | None
    new: AggregateRow | None

    def _value(self, side: str, attr: str) -> float:
        row = self.old if side == "old" else self.new
        return float(getattr(row, attr)) if row else 0.0

    @property
    def old_cpu(self) -> float:
        return self._value("old", "cpu_percent_total")

    @property
    def new_cpu(self) -> float:
        return self._value("new", "cpu_percent_total")

    @property
    def old_rss_mb(self) -> float:
        return self._value("old", "rss_mb_total")

    @property
    def new_rss_mb(self) -> float:
        return self._value("new", "rss_mb_total")

    @property
    def old_instances(self) -> int:
        return self.old.instances if self.old else 0

    @property
    def new_instances(self) -> int:
        return self.new.instances if self.new else 0

    @property
    def cpu_delta_pct(self) -> float:
        """Relative CPU delta as a percentage. Returns +inf when the old
        value was effectively zero and the new value is positive (any finite
        denominator there would be misleading)."""
        if self.old_cpu <= 0.01:
            return float("inf") if self.new_cpu > 0.01 else 0.0
        return ((self.new_cpu - self.old_cpu) / self.old_cpu) * 100.0

    @property
    def mem_delta_pct(self) -> float:
        """Relative memory delta as a percentage. +inf if old was ~0."""
        if self.old_rss_mb <= 0.5:
            return float("inf") if self.new_rss_mb > 0.5 else 0.0
        return ((self.new_rss_mb - self.old_rss_mb) / self.old_rss_mb) * 100.0

    @property
    def severity(self) -> float:
        """0..1000+. Used for color mapping. ``added`` returns +inf."""
        if self.status == "added":
            return float("inf")
        if self.status == "removed":
            return 0.0
        return max(self.cpu_delta_pct, self.mem_delta_pct, 0.0)


def aggregate_by_key(snapshot: ProcessSnapshot) -> dict[tuple[str, str], AggregateRow]:
    """Sum CPU% and RSS across every (name_lower, exe_lower) group in the snapshot."""
    groups: dict[tuple[str, str], AggregateRow] = {}
    for entry in snapshot.entries:
        key = (entry.name.lower(), entry.exe.lower())
        row = groups.get(key)
        if row is None:
            row = AggregateRow(key=key, name=entry.name, exe=entry.exe)
            groups[key] = row
        row.instances += 1
        row.cpu_percent_total += entry.cpu_percent
        row.rss_mb_total += entry.rss_mb
    return groups


def diff_snapshots(
    old: ProcessSnapshot,
    new: ProcessSnapshot,
    *,
    include_removed: bool = False,
    min_severity: float = 0.0,
) -> list[DiffEntry]:
    """Return per-(name, exe) diffs between two snapshots, sorted hottest first.

    ``min_severity`` filters out 'changed' rows whose max delta% is below the
    threshold — useful to hide noise. ``added`` rows always pass through.
    """
    a = aggregate_by_key(old)
    b = aggregate_by_key(new)
    entries: list[DiffEntry] = []

    for key, new_row in b.items():
        old_row = a.get(key)
        if old_row is None:
            entries.append(DiffEntry(
                key=key, name=new_row.name, exe=new_row.exe,
                status="added", old=None, new=new_row,
            ))
        else:
            entry = DiffEntry(
                key=key, name=new_row.name, exe=new_row.exe,
                status="changed", old=old_row, new=new_row,
            )
            if entry.severity >= min_severity:
                entries.append(entry)

    if include_removed:
        for key, old_row in a.items():
            if key not in b:
                entries.append(DiffEntry(
                    key=key, name=old_row.name, exe=old_row.exe,
                    status="removed", old=old_row, new=None,
                ))

    # Hottest first: added > changed (by severity) > removed.
    def _sort_key(e: DiffEntry):
        rank = {"added": 0, "changed": 1, "removed": 2}[e.status]
        sev = e.severity if e.severity != float("inf") else 1e9
        return (rank, -sev, e.name.lower())
    entries.sort(key=_sort_key)
    return entries


def diff_against_live(
    snapshot: ProcessSnapshot,
    live_processes: Iterable[psutil.Process] | None = None,
) -> list[psutil.Process]:
    """Return live processes whose (name, exe) is NOT in the snapshot.

    These are the 'new since snapshot' set — the smart-kill targets.
    """
    keys = snapshot.spare_keys()
    if live_processes is None:
        live_processes = psutil.process_iter(["name", "exe"], ad_value=None)
    new: list[psutil.Process] = []
    for proc in live_processes:
        try:
            info = proc.info if hasattr(proc, "info") else {}
            name = (info.get("name") or "").lower()
            exe = (info.get("exe") or "").lower()
            if (name, exe) not in keys:
                new.append(proc)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return new

"""Tests for MonitorLifecycle ordered teardown (T14)."""

from __future__ import annotations

import threading

from ui.monitor_lifecycle import MonitorLifecycle


def test_shutdown_calls_stop_in_registration_order_then_joins():
    events: list[str] = []
    lc = MonitorLifecycle()

    lc.register("a", stop=lambda: events.append("stop:a"), join=lambda t: events.append("join:a"))
    lc.register("b", stop=lambda: events.append("stop:b"), join=lambda t: events.append("join:b"))

    lc.shutdown(timeout_ms=100)

    assert events == ["stop:a", "stop:b", "join:a", "join:b"]


def test_shutdown_is_idempotent():
    calls = {"stop": 0, "join": 0}
    lc = MonitorLifecycle()
    lc.register(
        "only",
        stop=lambda: calls.__setitem__("stop", calls["stop"] + 1),
        join=lambda t: calls.__setitem__("join", calls["join"] + 1),
    )

    lc.shutdown()
    lc.shutdown()
    lc.shutdown()

    assert calls == {"stop": 1, "join": 1}
    assert lc.is_shut_down is True


def test_shutdown_swallows_stop_and_join_exceptions():
    lc = MonitorLifecycle()
    later: list[str] = []

    def bad_stop():
        raise RuntimeError("boom")

    def bad_join(_t):
        raise RuntimeError("boom2")

    lc.register("bad", stop=bad_stop, join=bad_join)
    lc.register("good", stop=lambda: later.append("stop"), join=lambda t: later.append("join"))

    # Must not raise even if a stop/join blows up.
    lc.shutdown(timeout_ms=50)

    assert later == ["stop", "join"]


def test_shutdown_joins_real_thread_with_signal_then_join_pattern():
    stop_event = threading.Event()
    started = threading.Event()

    def worker():
        started.set()
        while not stop_event.is_set():
            stop_event.wait(0.01)

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    started.wait(1.0)

    lc = MonitorLifecycle()
    lc.register("worker", stop=stop_event.set, join=lambda timeout_s: t.join(timeout=timeout_s))

    lc.shutdown(timeout_ms=2000)

    assert not t.is_alive()


def test_sensor_hub_stop_is_idempotent_and_finalizes_nvml(monkeypatch):
    from services.sensors import hub as hub_mod
    from services.sensors import nvml_backend

    shutdown_calls = {"n": 0}

    def fake_nvml_shutdown():
        shutdown_calls["n"] += 1

    monkeypatch.setattr(nvml_backend, "nvml_shutdown", fake_nvml_shutdown)

    h = hub_mod.SensorHub(source="auto")
    # Pretend it was started, with no thread and no backends, so stop just
    # exercises the idempotency + finalize path.
    h._started = True  # pylint: disable=protected-access
    h._backends = []  # pylint: disable=protected-access

    h.stop()
    h.stop()  # second call must be a no-op

    assert shutdown_calls["n"] == 1
    assert h._started is False  # pylint: disable=protected-access


def test_sensor_hub_stop_closes_backends_after_thread_joined(monkeypatch):
    from services.sensors import hub as hub_mod
    from services.sensors import nvml_backend

    monkeypatch.setattr(nvml_backend, "nvml_shutdown", lambda: None)

    events: list[str] = []

    class FakeBackend:
        id = "fake"

        def close(self):
            events.append("close")

    join_seen_alive = {"v": None}

    class FakeThread:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            join_seen_alive["v"] = True
            events.append("join")

    h = hub_mod.SensorHub(source="auto")
    h._started = True  # pylint: disable=protected-access
    h._backends = [FakeBackend()]  # pylint: disable=protected-access
    h._thread = FakeThread()  # pylint: disable=protected-access

    h.stop(timeout_s=0.5)

    # Reads (thread) must be stopped/joined BEFORE backend.close() runs.
    assert events == ["join", "close"]
    assert join_seen_alive["v"] is True

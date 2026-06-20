"""Tests for backend resolution and ordering.

These force CLR availability deterministically so they pass whether or not the
embedded DLL/pythonnet happen to be installed in the test environment.
"""

from __future__ import annotations

from services.sensors import resolver
from services.sensors.resolver import resolve


def _force_clr(monkeypatch, available: bool) -> None:
    monkeypatch.setattr(resolver.LhmClrBackend, "available", lambda self: available)


def test_auto_yields_http_first_when_clr_unavailable(monkeypatch):
    _force_clr(monkeypatch, False)
    ids = [b.id for b in resolve("auto")]
    assert ids[0] == "lhm-http"
    assert "lhm-clr" not in ids
    assert "pdh" in ids


def test_auto_leads_with_clr_when_available(monkeypatch):
    _force_clr(monkeypatch, True)
    ids = [b.id for b in resolve("auto")]
    assert ids[0] == "lhm-clr"
    assert "lhm-http" in ids


def test_explicit_sources_return_single_backend():
    assert [b.id for b in resolve("clr")] == ["lhm-clr"]
    assert [b.id for b in resolve("http")] == ["lhm-http"]


def test_unknown_source_falls_back_to_auto_chain(monkeypatch):
    _force_clr(monkeypatch, False)
    ids = [b.id for b in resolve("bogus")]
    assert ids[0] == "lhm-http"
    assert "pdh" in ids

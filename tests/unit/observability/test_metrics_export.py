# SPDX-License-Identifier: Apache-2.0
"""The read path for the Prometheus registry (FINDINGS A-56).

Every instrument in this repository was write-only: incremented in a container and never read.
These tests cover the endpoint that fixes that, and the two properties that keep it safe - it is
never served on the port the service routes traffic on, and it serves nothing but the registry.
"""

from __future__ import annotations

import socket
import urllib.error
import urllib.request

import pytest

from observability import metrics_export


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture(autouse=True)
def _stop_server() -> None:
    yield
    metrics_export.stop_metrics_server()


def test_the_metrics_port_is_never_the_port_the_service_serves_traffic_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sharing the routed port would publish the registry to the internet."""
    monkeypatch.setenv("PORT", "8080")
    monkeypatch.setenv("METRICS_PORT", "8080")
    with pytest.raises(metrics_export.MetricsExportError) as refused:
        metrics_export.metrics_port()
    assert "port of its own" in str(refused.value)


def test_a_misconfigured_port_does_not_stop_the_service_starting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telemetry that cannot start must not take the service down with it."""
    monkeypatch.setenv("PORT", "8080")
    monkeypatch.setenv("METRICS_PORT", "8080")
    assert metrics_export.start_metrics_server() is None


def test_the_endpoint_serves_the_registry_and_nothing_else(monkeypatch: pytest.MonkeyPatch) -> None:
    from prometheus_client import Counter

    probe = Counter("agenticorg_metrics_export_probe_total", "Exercised by the export test")
    probe.inc()

    port = _free_port()
    monkeypatch.setenv("METRICS_PORT", str(port))
    monkeypatch.delenv("PORT", raising=False)
    assert metrics_export.start_metrics_server() == port

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as response:
        body = response.read().decode()
        assert response.headers["Content-Type"].startswith("text/plain")
    assert "agenticorg_metrics_export_probe_total 1.0" in body

    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
    assert refused.value.code == 404


def test_multiprocess_mode_follows_the_directory(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """The Celery worker forks: without this the parent exports none of its children's work."""
    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    assert metrics_export.registry() is not None
    assert metrics_export.multiprocess_dir() == ""

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", str(tmp_path))
    assert metrics_export.multiprocess_dir() == str(tmp_path)
    merged = metrics_export.registry()
    # A registry built from the directory, not the process-local default one.
    assert merged is not metrics_export._DEFAULT_REGISTRY
    # Dropping a child's samples must not raise when the child left nothing behind.
    metrics_export.mark_process_dead(999_999)


def test_switching_to_multiprocess_after_an_instrument_exists_is_refused(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Silence is the failure mode here, so the late switch has to be loud.

    Reselecting the value class repairs a labelled instrument, whose per-child value is created
    on ``.labels()``. An unlabelled one binds its value at construction: it reads zero in the
    merged registry and a bare gauge vanishes from the exposition altogether.
    ``agenticorg_case_push_dead_letter_backlog`` is exactly that, and an alert reads it.
    """
    from prometheus_client import Gauge

    Gauge("agenticorg_metrics_export_late_probe", "Exercised by the export test")

    with pytest.raises(metrics_export.MetricsExportError) as refused:
        metrics_export.enable_multiprocess(str(tmp_path))
    assert "before importing the modules that define instruments" in str(refused.value)

    # The probe knows better: it creates its instruments after the switch.
    assert metrics_export.enable_multiprocess(str(tmp_path), allow_existing=True) == str(tmp_path)


def test_production_is_told_rather_than_stopped(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """A worker that refuses to start over a metrics problem is the worse trade."""
    from core.config import settings

    monkeypatch.setattr(settings, "env", "production", raising=False)
    assert metrics_export.enable_multiprocess(str(tmp_path)) == str(tmp_path)


def test_the_client_s_own_collectors_do_not_count_as_instruments() -> None:
    """process, platform and GC collectors are registered on import and are never at risk."""
    names = metrics_export._instruments_already_registered()
    assert not [name for name in names if name.startswith(("python_", "process_"))]

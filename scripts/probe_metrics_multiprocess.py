# SPDX-License-Identifier: Apache-2.0
"""Prove that a forked child's metrics reach the exporter (POSIX only).

The Celery worker runs Celery's prefork pool: tasks execute in forked children while the process
that serves ``/metrics`` is the parent. If multiprocess mode is not working, the endpoint reports
the parent's own activity and nothing else - and it reports it perfectly happily, which is why
this is worth proving rather than assuming.

It forks two children that each record a value, scrapes the endpoint, and checks:

* a counter totals the parent's and both children's work;
* a ``livesum`` gauge totals all three while they are alive;
* after ``mark_process_dead``, the counter still totals everything (the work happened) while the
  gauge drops to the parent's value alone (the workers are gone).

Run it directly, or in CI on Linux. It exits non-zero on the first mismatch.

Adapted from the probe written during the independent review of the metrics export.
"""

from __future__ import annotations

import glob
import multiprocessing
import os
import sys
import tempfile
from http.client import HTTPConnection
from typing import Any

EXPECTED_TOTAL = 15.0  # parent 5 + children 3 and 7
EXPECTED_LIVE_AFTER_DEATH = 5.0  # the parent alone


# The instruments are created once, in the parent, before it forks. That is how the worker does it
# too - a child inherits the registry rather than building its own - and re-creating them in the
# child would collide with the inherited copy.
_COUNTER: Any = None
_GAUGE: Any = None


def _child(value: int) -> None:
    _COUNTER.labels(who=f"child{value}").inc(value)
    _GAUGE.labels(who=f"child{value}").set(value)


def _scrape(port: int) -> str:
    """Read the exposition text over loopback (http.client, so no URL scheme can be anything else)."""
    connection = HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request("GET", "/metrics")
        return connection.getresponse().read().decode()
    finally:
        connection.close()


def _sum(body: str, prefix: str) -> float:
    return sum(float(line.rsplit(" ", 1)[1]) for line in body.splitlines() if line.startswith(prefix))


def main() -> int:
    if not hasattr(os, "fork"):
        print("skipped: this probe is about fork, and this platform does not fork")
        return 0

    os.environ.setdefault("PROMETHEUS_MULTIPROC_DIR", tempfile.mkdtemp(prefix="agenticorg-mp-"))
    directory = os.environ["PROMETHEUS_MULTIPROC_DIR"]
    os.makedirs(directory, exist_ok=True)
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    from prometheus_client import Counter, Gauge

    from observability import metrics_export

    global _COUNTER, _GAUGE
    _COUNTER = Counter("agenticorg_mp_probe_total", "probe", ["who"])
    _GAUGE = Gauge("agenticorg_mp_gauge", "probe gauge", ["who"], multiprocess_mode="livesum")
    _COUNTER.labels(who="parent").inc(5)
    _GAUGE.labels(who="parent").set(5)

    children = []
    for value in (3, 7):
        process = multiprocessing.Process(target=_child, args=(value,))
        process.start()
        children.append(process)
    for process in children:
        process.join()

    failures: list[str] = []
    print(f"start method: {multiprocessing.get_start_method()}")
    print(f"sample files written: {len(glob.glob(os.path.join(directory, '*.db')))}")

    port = metrics_export.start_metrics_server(serving_port=0)
    if port is None:
        print("FAILED: the exporter did not start")
        return 1
    try:
        body = _scrape(port)
        counter = _sum(body, "agenticorg_mp_probe_total{")
        gauge = _sum(body, "agenticorg_mp_gauge{")
        print(f"counter across processes: {counter} (expected {EXPECTED_TOTAL})")
        print(f"gauge livesum while alive: {gauge} (expected {EXPECTED_TOTAL})")
        if counter != EXPECTED_TOTAL:
            failures.append("a forked child's counter never reached the exporter")
        if gauge != EXPECTED_TOTAL:
            failures.append("a forked child's gauge never reached the exporter")

        for process in children:
            metrics_export.mark_process_dead(process.pid)
        body = _scrape(port)
        counter = _sum(body, "agenticorg_mp_probe_total{")
        gauge = _sum(body, "agenticorg_mp_gauge{")
        print(f"counter after the children exited: {counter} (expected {EXPECTED_TOTAL}, the work happened)")
        print(f"gauge livesum after they exited: {gauge} (expected {EXPECTED_LIVE_AFTER_DEATH})")
        if counter != EXPECTED_TOTAL:
            failures.append("a dead child's completed work was dropped from a counter")
        if gauge != EXPECTED_LIVE_AFTER_DEATH:
            failures.append("a dead child is still reported as a live gauge")
    finally:
        metrics_export.stop_metrics_server()

    for failure in failures:
        print(f"FAILED: {failure}", file=sys.stderr)
    if not failures:
        print("multiprocess aggregation OK")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Foundation #7 PR-E — fake celery hermetic seam regressions.

Pinned behaviors:

- ``is_active()`` reflects the env var.
- The Celery app loaded under the flag has
  ``task_always_eager=True`` and ``task_eager_propagates=True``.
- ``.delay()`` runs the task body synchronously.
- ``task_prerun`` signal records each invocation in
  ``fake_celery.invocations()``.
- ``find(task_name=...)`` filters by name; ``last()`` returns
  most recent.
- The conftest sets the flag by default; autouse fixture resets
  the invocation list between tests.

Notes on the test design:
- We can't safely re-import ``core.tasks.celery_app`` mid-test
  (Celery binds signals globally) so the seam-active assertions
  read the live app config.
- The eager-execution assertions use a tiny in-test task
  registered against the production app under a unique name.
"""

from __future__ import annotations

import os

import pytest

from core.test_doubles import fake_celery


def test_is_active_reflects_env_var(monkeypatch) -> None:
    monkeypatch.delenv("AGENTICORG_TEST_FAKE_CELERY", raising=False)
    assert fake_celery.is_active() is False
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_CELERY", "1")
    assert fake_celery.is_active() is True
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_CELERY", "true")
    assert fake_celery.is_active() is True
    monkeypatch.setenv("AGENTICORG_TEST_FAKE_CELERY", "no")
    assert fake_celery.is_active() is False


def test_record_appends_to_invocations() -> None:
    assert fake_celery.count() == 0
    fake_celery._record(name="some.task", args=(1, 2), kwargs={"k": "v"})
    assert fake_celery.count() == 1
    rec = fake_celery.last()
    assert rec is not None
    assert rec.name == "some.task"
    assert rec.args == (1, 2)
    assert rec.kwargs == {"k": "v"}


def test_find_filters_by_task_name() -> None:
    fake_celery._record(name="a.task")
    fake_celery._record(name="b.task")
    fake_celery._record(name="a.task")
    assert len(fake_celery.find(task_name="a.task")) == 2
    assert len(fake_celery.find(task_name="b.task")) == 1
    assert fake_celery.find(task_name="missing") == []


def test_reset_clears_invocations() -> None:
    fake_celery._record(name="x.task")
    assert fake_celery.count() == 1
    fake_celery.reset()
    assert fake_celery.count() == 0
    assert fake_celery.last() is None


def test_celery_app_runs_in_eager_mode_under_flag() -> None:
    """When the flag is on, the production Celery app must have
    eager + propagates enabled so .delay() runs synchronously."""
    assert os.environ.get("AGENTICORG_TEST_FAKE_CELERY") == "1"
    from core.tasks.celery_app import app

    assert app.conf.task_always_eager is True
    assert app.conf.task_eager_propagates is True


def test_delay_runs_synchronously_and_captures_invocation() -> None:
    """End-to-end: registering a task on the production app, calling
    .delay(), and asserting both that the body ran AND that the
    invocation was captured by the prerun signal."""
    from core.tasks.celery_app import app

    # Register an in-test task. Unique name so it can't collide with
    # production tasks even if somehow re-discovered.
    side_effects: list[int] = []

    @app.task(name="tests.fake_celery.synchronous_marker")
    def _marker(value: int) -> int:
        side_effects.append(value * 2)
        return value * 2

    result = _marker.delay(7).get(timeout=1)
    assert result == 14
    assert side_effects == [14]

    # Captured by the task_prerun signal handler.
    captures = fake_celery.find(task_name="tests.fake_celery.synchronous_marker")
    assert len(captures) == 1
    # Celery delivers args as a tuple to the signal.
    assert captures[0].args == (7,)


def test_propagates_exceptions_under_eager() -> None:
    """When task_eager_propagates is True, a failing task raises
    the underlying exception in the caller — not just leaves a
    failed AsyncResult. This is what makes unit tests
    ``with pytest.raises(...)`` actually work."""
    from core.tasks.celery_app import app

    @app.task(name="tests.fake_celery.boom")
    def _boom() -> None:
        raise ValueError("simulated task failure")

    with pytest.raises(ValueError, match="simulated task failure"):
        _boom.delay().get(timeout=1)


def test_conftest_default_makes_fake_celery_active() -> None:
    assert os.environ.get("AGENTICORG_TEST_FAKE_CELERY") == "1"
    assert fake_celery.is_active() is True


def test_autouse_fixture_resets_part_1() -> None:
    fake_celery._record(name="bleed.task")
    assert fake_celery.count() == 1


def test_autouse_fixture_resets_part_2() -> None:
    """Bleed-check second half — must see clean state."""
    assert fake_celery.count() == 0


def test_deactivate_unlatches_eager_mode_then_reactivate_restores() -> None:
    """Codex PR-E P1: eager mode must be reversible. The earlier
    design flipped ``task_always_eager=True`` at celery_app import
    time gated on the env var, which permanently latched the flag
    on the singleton ``app`` once the conftest set the env var.
    Tests (or the integration-tests CI job) that tried to opt back
    to a real broker by clearing the env var STILL ran eagerly,
    silently turning broker round-trip tests into in-process eager
    runs — the false-green pattern Foundation #8 forbids.

    The fix moved activation into ``fake_celery.activate(app)`` /
    ``deactivate(app)`` so the eager flag is observable + reversible
    on the live app config. This pin verifies the round-trip.
    """
    from core.tasks.celery_app import app

    # Conftest already called activate(app) → eager is on.
    assert app.conf.task_always_eager is True
    assert app.conf.task_eager_propagates is True

    fake_celery.deactivate(app)
    try:
        assert app.conf.task_always_eager is False
        assert app.conf.task_eager_propagates is False
    finally:
        # Restore so subsequent tests in this module + later modules
        # don't inherit broker-dispatch mode (which would hang on
        # any .delay() because there's no real broker in unit CI).
        fake_celery.activate(app)

    assert app.conf.task_always_eager is True
    assert app.conf.task_eager_propagates is True


def test_activate_is_idempotent() -> None:
    """Calling ``activate(app)`` twice must not double-install the
    task_prerun signal handler — that would cause every executed
    task to be captured twice in ``invocations()``."""
    from core.tasks.celery_app import app

    fake_celery.activate(app)
    fake_celery.activate(app)  # second call — must be a no-op for the signal

    @app.task(name="tests.fake_celery.idempotent_marker")
    def _marker() -> int:
        return 1

    _marker.delay().get(timeout=1)
    captures = fake_celery.find(task_name="tests.fake_celery.idempotent_marker")
    assert len(captures) == 1, (
        f"expected exactly one capture, got {len(captures)} "
        "— signal handler was installed multiple times"
    )

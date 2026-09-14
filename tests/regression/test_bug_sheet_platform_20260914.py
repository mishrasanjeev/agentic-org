"""Bug sheet 2026-09-14 — platform items #8, #10, #12, #13.

#8  Container logs: single-line JSON records + end-to-end request correlation
    (X-Request-ID middleware, structlog contextvars, Celery header propagation,
    SQL echo no longer tied to env=development).
#10 Every APIRouter defined under api/v1 is actually mounted on the app.
#12 Cloud Run worker/beat services get an explicit --command/--args so a
    service created from the API image does not boot uvicorn.
#13 Grantex issuer/base URL resolves per environment instead of defaulting
    every environment to the production issuer.
"""

from __future__ import annotations

import importlib
import io
import json
import logging
import pkgutil
import shutil
import subprocess
import uuid
import weakref
from pathlib import Path
from types import SimpleNamespace

import pytest
import structlog
import structlog.contextvars
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from api.middleware.request_id import RequestIDMiddleware, normalize_request_id
from core import config as config_mod
from core.logging_config import build_formatter, configure_logging

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# #8 Request correlation middleware
# ---------------------------------------------------------------------------


def _mini_app() -> FastAPI:
    app = FastAPI()

    @app.get("/echo")
    async def echo():
        structlog.get_logger("tests.request_id").info("inside_route")
        return structlog.contextvars.get_contextvars()

    app.add_middleware(RequestIDMiddleware)
    return app


def test_valid_x_request_id_is_kept_bound_and_echoed(caplog):
    configure_logging()
    client = TestClient(_mini_app())
    with caplog.at_level(logging.INFO, logger="tests.request_id"):
        resp = client.get("/echo", headers={"X-Request-ID": "req-abc.123_X"})
    assert resp.status_code == 200
    assert resp.headers["x-request-id"] == "req-abc.123_X"
    body = resp.json()
    assert body["request_id"] == "req-abc.123_X"
    assert body["method"] == "GET"
    assert body["path"] == "/echo"
    # The structlog record reached stdlib logging with the contextvars merged in.
    events = [r.msg for r in caplog.records if isinstance(r.msg, dict) and r.msg.get("event") == "inside_route"]
    assert events, "structlog event was not routed through stdlib logging"
    assert events[0]["request_id"] == "req-abc.123_X"
    # Context is cleared once the response is sent.
    assert structlog.contextvars.get_contextvars() == {}


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "x" * 129, "has space", "semi;colon", "<script>", "a/b", "tab\there"],
)
def test_invalid_x_request_id_is_replaced_with_uuid4(bad):
    client = TestClient(_mini_app())
    resp = client.get("/echo", headers={"X-Request-ID": bad})
    echoed = resp.headers["x-request-id"]
    assert echoed != bad
    uuid.UUID(echoed)  # raises if not a uuid
    assert resp.json()["request_id"] == echoed
    # Non-ASCII / control characters never survive normalisation either.
    for raw in ("ünïcode", "new\nline", "\x00"):
        uuid.UUID(normalize_request_id(raw))


def test_missing_x_request_id_is_generated():
    client = TestClient(_mini_app())
    resp = client.get("/echo")
    uuid.UUID(resp.headers["x-request-id"])
    assert normalize_request_id(None) != normalize_request_id(None)


def test_request_id_middleware_is_outermost_on_the_real_app():
    from api.main import app

    # Starlette: the LAST add_middleware call is the outermost layer.
    assert app.user_middleware[0].cls is RequestIDMiddleware
    # A 401 produced by the auth middleware still carries the correlation id.
    client = TestClient(app)
    resp = client.get("/api/v1/agents", headers={"X-Request-ID": "corr-401"})
    assert resp.status_code == 401
    assert resp.headers["x-request-id"] == "corr-401"


# ---------------------------------------------------------------------------
# #8 Single-line JSON rendering for structlog AND stdlib records
# ---------------------------------------------------------------------------


def _json_lines_for(emit) -> list[dict]:
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setFormatter(build_formatter("json"))
    probe = logging.getLogger("tests.json_probe")
    probe.propagate = False
    probe.addHandler(handler)
    probe.setLevel(logging.DEBUG)
    try:
        emit(probe)
    finally:
        probe.removeHandler(handler)
        probe.propagate = True
    lines = buf.getvalue().splitlines()
    assert lines
    return [json.loads(line) for line in lines]


def test_json_formatter_emits_one_line_per_record_including_exceptions():
    configure_logging()
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="rid-json")

    def emit(probe: logging.Logger) -> None:
        # structlog record with a traceback
        slog = structlog.wrap_logger(probe, wrapper_class=structlog.stdlib.BoundLogger)
        try:
            raise RuntimeError("structlog boom")
        except RuntimeError:
            slog.exception("structlog_failure", extra_field=1)
        # foreign stdlib record with %-args and a traceback
        try:
            raise ValueError("stdlib boom")
        except ValueError:
            probe.exception("stdlib failure for %s", "sqlalchemy")
        probe.warning("plain %s", "warning")

    try:
        records = _json_lines_for(emit)
    finally:
        structlog.contextvars.clear_contextvars()

    assert len(records) == 3, records
    structlog_rec, stdlib_rec, plain_rec = records
    assert structlog_rec["event"] == "structlog_failure"
    assert structlog_rec["extra_field"] == 1
    assert structlog_rec["level"] == "error"
    assert "Traceback" in structlog_rec["exception"] and "structlog boom" in structlog_rec["exception"]
    assert stdlib_rec["event"] == "stdlib failure for sqlalchemy"
    assert "stdlib boom" in stdlib_rec["exception"]
    assert plain_rec["event"] == "plain warning" and plain_rec["level"] == "warning"
    for rec in records:
        assert rec["request_id"] == "rid-json"
        assert rec["timestamp"].endswith("Z")


def _settings_for(env: str, explicit_format: str | None = None) -> config_mod.Settings:
    fields = {"env"}
    if explicit_format:
        fields.add("log_format")
    return config_mod.Settings.model_construct(
        _fields_set=fields, env=env, log_level="INFO", log_format=explicit_format or "json"
    )


@pytest.mark.parametrize(
    ("env", "explicit_format", "expected"),
    [
        ("test", None, "console"),
        ("production", None, "json"),
        ("staging", None, "json"),
        ("development", None, "json"),
        ("test", "json", "json"),
        ("production", "console", "console"),
        ("production", "bogus", "json"),
    ],
)
def test_log_format_defaults_to_console_only_for_test_env(monkeypatch, env, explicit_format, expected):
    from core import logging_config

    monkeypatch.setattr(logging_config, "settings", _settings_for(env, explicit_format))
    assert logging_config.resolve_log_format() == expected


def test_capture_logs_still_works_after_configure():
    configure_logging()
    with structlog.testing.capture_logs() as captured:
        structlog.get_logger("tests.capture").info("captured_event", k="v")
    assert captured == [{"event": "captured_event", "k": "v", "log_level": "info"}]


def test_sql_echo_is_opt_in_not_tied_to_development():
    from core.database import engine

    assert config_mod.Settings.model_fields["db_echo"].default is False
    assert engine.echo is False
    src = (REPO_ROOT / "core" / "database.py").read_text(encoding="utf-8")
    assert 'echo=settings.env == "development"' not in src
    assert "echo=settings.db_echo" in src


# ---------------------------------------------------------------------------
# #8 Celery request-id propagation
# ---------------------------------------------------------------------------


def test_before_task_publish_copies_request_id_into_headers():
    from core.tasks import celery_app as ca

    structlog.contextvars.clear_contextvars()
    headers: dict = {}
    ca.propagate_request_id_to_task(headers=headers)
    assert headers == {}  # nothing bound -> nothing forged
    structlog.contextvars.bind_contextvars(request_id="rid-pub")
    try:
        ca.propagate_request_id_to_task(headers=headers)
        assert headers == {"request_id": "rid-pub"}
        ca.propagate_request_id_to_task(headers={"request_id": "explicit"})  # explicit header wins
        ca.propagate_request_id_to_task(headers=None)  # tolerated
    finally:
        structlog.contextvars.clear_contextvars()


def test_task_prerun_binds_header_request_id_and_postrun_restores_context():
    from core.tasks import celery_app as ca

    structlog.contextvars.clear_contextvars()
    task = SimpleNamespace(
        name="core.tasks.report_tasks.generate_report",
        request=SimpleNamespace(get=lambda key, default=None: {"request_id": "rid-from-header"}.get(key, default)),
    )
    ca.bind_task_log_context(task_id="task-1", task=task)
    ctx = structlog.contextvars.get_contextvars()
    assert ctx["request_id"] == "rid-from-header"
    assert ctx["task_id"] == "task-1"
    assert ctx["task_name"] == "core.tasks.report_tasks.generate_report"
    ca.clear_task_log_context(task_id="task-1")
    assert structlog.contextvars.get_contextvars() == {}


def test_task_prerun_falls_back_to_task_id_and_eager_run_keeps_caller_context():
    from core.tasks import celery_app as ca

    structlog.contextvars.clear_contextvars()
    # Eager mode (tests / in-request .delay()): the caller's request_id must
    # survive the task's prerun/postrun pair.
    structlog.contextvars.bind_contextvars(request_id="rid-caller")
    try:
        task = SimpleNamespace(name="t", request=SimpleNamespace(headers=None, get=lambda *_a: None))
        ca.bind_task_log_context(task_id="task-2", task=task)
        assert structlog.contextvars.get_contextvars()["request_id"] == "task-2"
        ca.clear_task_log_context(task_id="task-2")
        assert structlog.contextvars.get_contextvars() == {"request_id": "rid-caller"}
    finally:
        structlog.contextvars.clear_contextvars()


def _live_receivers(signal) -> list:
    out = []
    for _key, receiver in signal.receivers:
        out.append(receiver() if isinstance(receiver, weakref.ReferenceType) else receiver)
    return out


def test_celery_logging_and_correlation_signals_are_connected():
    from celery.signals import before_task_publish, setup_logging, task_postrun, task_prerun

    from core.tasks import celery_app as ca

    # setup_logging having a listener is what stops Celery hijacking the root logger.
    assert setup_logging.has_listeners()
    assert ca._configure_celery_logging in _live_receivers(setup_logging)
    assert ca.propagate_request_id_to_task in _live_receivers(before_task_publish)
    assert ca.bind_task_log_context in _live_receivers(task_prerun)
    assert ca.clear_task_log_context in _live_receivers(task_postrun)


# ---------------------------------------------------------------------------
# #12 Cloud Run worker/beat command
# ---------------------------------------------------------------------------


def test_deploy_script_pins_worker_and_beat_entrypoints():
    script_path = REPO_ROOT / "scripts" / "deploy_cloud_run.sh"
    script = script_path.read_text(encoding="utf-8")
    assert (REPO_ROOT / "scripts" / "run_worker.py").is_file()
    assert (REPO_ROOT / "scripts" / "run_beat.py").is_file()
    assert 'worker) entrypoint="scripts/run_worker.py" ;;' in script
    assert 'beat) entrypoint="scripts/run_beat.py" ;;' in script
    assert (
        'update_service_no_traffic new_revision "$svc" "$API_IMAGE" "$BACKGROUND_UPDATE_ENV_VARS" '
        '"$label" "$API_IMAGE_DIGEST" "AGENTICORG_GIT_SHA" --command=python "--args=$entrypoint"'
    ) in script
    # update_service_no_traffic forwards the extra flags to gcloud.
    start = script.index("update_service_no_traffic() {")
    end = script.index("set_probe_tag() {", start)
    body = script[start:end]
    assert "shift 7" in body
    assert 'local extra_args=("$@")' in body
    assert "--no-traffic \\\n    ${extra_args[@]+\"${extra_args[@]}\"}" in body
    # The printed manual rollout commands keep the entrypoint pinned too.
    manual_start = script.index("print_manual_traffic_commands() {")
    manual = script[manual_start : script.index("\n}\n", manual_start)]
    assert "--command=python --args=scripts/run_worker.py" in manual
    assert "--command=python --args=scripts/run_beat.py" in manual
    assert "for svc in $WORKER_SERVICE $BEAT_SERVICE" not in manual
    bash = shutil.which("bash")
    if bash:
        subprocess.run([bash, "-n", str(script_path)], check=True)  # noqa: S603


def test_worker_and_beat_commands_are_consistent_across_compose_and_cloud_run():
    compose = (REPO_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "command: python scripts/run_beat.py" in compose
    assert "celery -A core.tasks.celery_app worker" in compose
    worker_src = (REPO_ROOT / "scripts" / "run_worker.py").read_text(encoding="utf-8")
    assert "core.tasks.celery_app" in worker_src


# ---------------------------------------------------------------------------
# #13 Grantex issuer per environment
# ---------------------------------------------------------------------------


@pytest.fixture
def no_explicit_grantex(monkeypatch):
    monkeypatch.delenv("GRANTEX_BASE_URL", raising=False)
    monkeypatch.delenv("AGENTICORG_GRANTEX_ISSUER", raising=False)
    monkeypatch.setattr(config_mod.settings, "grantex_issuer", "")
    monkeypatch.setattr(config_mod, "external_keys", config_mod.ExternalKeys.model_construct())


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ("production", "https://api.grantex.dev"),
        ("prod", "https://api.grantex.dev"),
        ("staging", "https://api-staging.grantex.dev"),
        ("stage", "https://api-staging.grantex.dev"),
        ("Staging", "https://api-staging.grantex.dev"),
        ("uat", "https://api-staging.grantex.dev"),
        ("preview", "https://api-staging.grantex.dev"),
        ("development", "https://api.grantex.dev"),
        ("dev", "https://api.grantex.dev"),
        ("local", "https://api.grantex.dev"),
        ("test", "https://api.grantex.dev"),
        ("", "https://api.grantex.dev"),
    ],
)
def test_grantex_base_url_derives_from_environment(no_explicit_grantex, monkeypatch, env, expected):
    assert config_mod.grantex_base_url_for_env(env) == expected
    monkeypatch.setattr(config_mod.settings, "env", env)
    assert config_mod.grantex_base_url_for_env() == expected


def test_grantex_staging_envs_are_the_non_production_strict_runtimes():
    assert config_mod.GRANTEX_STAGING_ENVS == frozenset({"staging", "stage", "preview", "uat"})
    assert config_mod.GRANTEX_STAGING_ENVS - {"uat"} <= config_mod.STRICT_ENVS
    for env in config_mod.GRANTEX_STAGING_ENVS:
        assert config_mod.is_strict_runtime_env(env), env
    for env in config_mod.RELAXED_ENVS | config_mod.GRANTEX_PRODUCTION_ENVS:
        assert env not in config_mod.GRANTEX_STAGING_ENVS, env


@pytest.mark.parametrize(
    ("env", "expected_issuer"),
    [
        # The SDK maps the production JWKS alias to its canonical issuer.
        ("production", "https://grantex.dev"),
        ("development", "https://grantex.dev"),
        ("staging", "https://api-staging.grantex.dev"),
        ("uat", "https://api-staging.grantex.dev"),
        ("preview", "https://api-staging.grantex.dev"),
    ],
)
def test_grantex_issuer_resolves_per_environment(no_explicit_grantex, env, expected_issuer):
    assert config_mod.grantex_issuer_for_env(env) == expected_issuer


def test_grantex_issuer_override_precedence(no_explicit_grantex, monkeypatch):
    # 1. raw env var beats the derived default
    monkeypatch.setenv("AGENTICORG_GRANTEX_ISSUER", "https://env-issuer.example/")
    assert config_mod.grantex_issuer_for_env("staging") == "https://env-issuer.example"
    # 2. the parsed setting beats the raw env var
    monkeypatch.setattr(config_mod.settings, "grantex_issuer", "https://settings-issuer.example")
    assert config_mod.grantex_issuer_for_env("staging") == "https://settings-issuer.example"
    # 3. GRANTEX_BASE_URL moves the derived issuer when no issuer override exists
    monkeypatch.setattr(config_mod.settings, "grantex_issuer", "")
    monkeypatch.delenv("AGENTICORG_GRANTEX_ISSUER")
    monkeypatch.setenv("GRANTEX_BASE_URL", "https://self-hosted.example/grantex/")
    assert config_mod.grantex_issuer_for_env("production") == "https://self-hosted.example/grantex"
    assert config_mod.grantex_jwks_uri_for_env("production") == (
        "https://self-hosted.example/grantex/.well-known/jwks.json"
    )


def test_explicit_grantex_configuration_wins_over_env_default(no_explicit_grantex, monkeypatch):
    from auth import grantex_middleware as gm

    monkeypatch.setenv("GRANTEX_BASE_URL", "https://grantex.internal.example/")
    for env in ("staging", "uat", "production", "development"):
        assert config_mod.grantex_base_url_for_env(env) == "https://grantex.internal.example"
    assert gm.grantex_jwks_uri() == "https://grantex.internal.example/.well-known/jwks.json"
    # A value from .env (ExternalKeys) is honoured too, env var still wins.
    monkeypatch.delenv("GRANTEX_BASE_URL")
    monkeypatch.setattr(
        config_mod,
        "external_keys",
        config_mod.ExternalKeys.model_construct(
            _fields_set={"grantex_base_url"}, grantex_base_url="https://dotenv.example/"
        ),
    )
    assert config_mod.grantex_base_url_for_env("staging") == "https://dotenv.example"
    monkeypatch.setenv("GRANTEX_BASE_URL", "https://envvar.example")
    assert config_mod.grantex_base_url_for_env("staging") == "https://envvar.example"
    # The issuer override applies to token verification without moving the JWKS host.
    monkeypatch.setenv("AGENTICORG_GRANTEX_ISSUER", "https://issuer.example/")
    assert gm.grantex_expected_issuer() == "https://issuer.example"
    assert gm.grantex_jwks_uri() == "https://envvar.example/.well-known/jwks.json"


def test_middleware_registration_and_langgraph_client_agree_on_grantex_origin(no_explicit_grantex, monkeypatch):
    import grantex
    from grantex._verify import _derive_issuer_from_jwks_uri

    from auth import grantex_middleware as gm
    from auth import grantex_registration as gr
    from core.langgraph import grantex_auth as ga

    monkeypatch.setattr(config_mod.settings, "env", "staging")
    expected = "https://api-staging.grantex.dev"

    assert gm.grantex_jwks_uri() == f"{expected}/.well-known/jwks.json"
    staging_issuer = gm.grantex_expected_issuer()
    assert staging_issuer == _derive_issuer_from_jwks_uri(gm.grantex_jwks_uri()).rstrip("/")
    assert "api-staging.grantex.dev" in staging_issuer

    seen: list[str] = []

    class FakeGrantex:
        def __init__(self, api_key: str, base_url: str):
            seen.append(base_url)

    monkeypatch.setenv("GRANTEX_API_KEY", "gx_test")
    monkeypatch.setattr(grantex, "Grantex", FakeGrantex)
    assert gr._get_grantex_client() is not None
    monkeypatch.setattr(ga, "Grantex", FakeGrantex)
    monkeypatch.setattr(ga, "_load_all_manifests", lambda _client: None)
    monkeypatch.setattr(ga, "_grantex_client", None)
    ga.get_grantex_client()
    monkeypatch.setattr(ga, "_grantex_client", None)
    assert seen == [expected, expected]

    # Production stays on the production issuer, which is distinct from staging.
    monkeypatch.setattr(config_mod.settings, "env", "production")
    assert gm.grantex_jwks_uri() == "https://api.grantex.dev/.well-known/jwks.json"
    production_issuer = gm.grantex_expected_issuer()
    assert production_issuer == _derive_issuer_from_jwks_uri(gm.grantex_jwks_uri()).rstrip("/")
    assert production_issuer.endswith("grantex.dev") and "staging" not in production_issuer
    assert production_issuer != staging_issuer


def test_no_module_hardcodes_the_production_grantex_default_anymore():
    for rel in ("auth/grantex_middleware.py", "auth/grantex_registration.py", "core/langgraph/grantex_auth.py"):
        src = (REPO_ROOT / rel).read_text(encoding="utf-8")
        assert 'os.getenv("GRANTEX_BASE_URL"' not in src, rel
        resolvers = ("grantex_base_url_for_env(", "grantex_jwks_uri_for_env(", "grantex_issuer_for_env(")
        assert any(name in src for name in resolvers), rel


def test_env_example_documents_grantex_issuer_settings():
    env_example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    for key in (
        "GRANTEX_BASE_URL=",
        "AGENTICORG_GRANTEX_ISSUER=",
        "AGENTICORG_GRANTEX_AUDIENCE=",
        "AGENTICORG_LOG_FORMAT=",
        "AGENTICORG_DB_ECHO=",
        "AGENTICORG_UI_BASE_URL=",
    ):
        assert key in env_example, key


# ---------------------------------------------------------------------------
# #10 Every api/v1 router is mounted
# ---------------------------------------------------------------------------


def _iter_app_routes(routes):
    """Walk APIRoutes, mounted routers and FastAPI>=0.139 ``_IncludedRouter`` entries."""
    for route in routes:
        yield route
        nested = getattr(route, "routes", None)
        if nested:
            yield from _iter_app_routes(nested)
        original = getattr(route, "original_router", None)
        if original is not None:
            yield from _iter_app_routes(original.routes)


def test_every_api_v1_router_is_mounted_on_the_app():
    import api.v1
    from api.main import app

    mounted_endpoints = {
        getattr(route, "endpoint", None) for route in _iter_app_routes(app.routes) if hasattr(route, "endpoint")
    }
    routers_seen = 0
    missing: list[str] = []
    for mod_info in pkgutil.iter_modules(api.v1.__path__):
        module = importlib.import_module(f"api.v1.{mod_info.name}")
        for attr_name, value in vars(module).items():
            if not isinstance(value, APIRouter):
                continue
            routers_seen += 1
            for route in value.routes:
                endpoint = getattr(route, "endpoint", None)
                if endpoint is None:
                    continue
                if endpoint not in mounted_endpoints:
                    missing.append(f"api.v1.{mod_info.name}.{attr_name}: {getattr(route, 'path', route)}")
    assert routers_seen >= 60, routers_seen
    assert not missing, "api/v1 routes never mounted on api.main.app:\n" + "\n".join(missing)


def test_json_log_lines_carry_cloud_logging_severity() -> None:
    """Production runs on Cloud Run: Cloud Logging classifies JSON entries by a
    top-level ``severity`` field, not ``level``. Without it ERROR records are
    stored as DEFAULT and severity-based alerting silently stops matching."""
    import io
    import json as _json
    import logging as _logging

    from core.logging_config import build_formatter

    stream = io.StringIO()
    handler = _logging.StreamHandler(stream)
    handler.setFormatter(build_formatter("json"))
    log = _logging.getLogger("bug_sheet_severity_probe")
    log.handlers = [handler]
    log.propagate = False
    log.setLevel(_logging.DEBUG)
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        log.exception("probe failed")
    log.warning("probe warned")
    lines = [ln for ln in stream.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 2, lines
    first, second = (_json.loads(ln) for ln in lines)
    assert first["severity"] == "ERROR" and "boom" in first["exception"]
    assert second["severity"] == "WARNING"


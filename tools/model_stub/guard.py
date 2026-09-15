# SPDX-License-Identifier: Apache-2.0
"""Runtime guard for the model stub, importable before any application module.

``core.config`` validates production settings at import time, so the guard
lives here and runs first: outside development and test the stub stops with a
clear reason instead of a configuration traceback.
"""

from __future__ import annotations

from collections.abc import Mapping

ALLOWED_ENVS = frozenset({"development", "dev", "local", "test"})
PRODUCTION_MARKERS = frozenset({"production", "prod", "staging", "stage", "uat", "preprod", "live"})
ENV_VARIABLES = ("AGENTICORG_ENV", "APP_ENV", "ENVIRONMENT", "ENV", "NODE_ENV")


class StartupRefusedError(RuntimeError):
    """The stub must not run with this environment."""


def assert_development_runtime(environ: Mapping[str, str]) -> str:
    for name in ENV_VARIABLES:
        value = environ.get(name, "").strip().lower()
        if value in PRODUCTION_MARKERS:
            raise StartupRefusedError(
                f"{name}={value} indicates a production-like runtime; the model stub only runs in development and test"
            )
    runtime = environ.get("AGENTICORG_ENV", "").strip().lower()
    if runtime not in ALLOWED_ENVS:
        raise StartupRefusedError(
            f"AGENTICORG_ENV must be one of {', '.join(sorted(ALLOWED_ENVS))} to run the model stub "
            f"(got {runtime or 'nothing'})"
        )
    return runtime

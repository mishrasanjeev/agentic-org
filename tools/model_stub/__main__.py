# SPDX-License-Identifier: Apache-2.0
"""Run the development model stub: ``python -m tools.model_stub``.

Environment:

* ``AGENTICORG_ENV`` - must be ``development``, ``local`` or ``test``; a
  production-like value in the usual environment variables refuses to start.
* ``MODEL_STUB_MODE`` - ``replay`` (default) or ``record``. Record refuses to
  start without ``MODEL_RECORD_API_KEY``.
* ``MODEL_STUB_CASSETTE_DIR`` - cassettes (default ``/cassettes``).
* ``MODEL_STUB_SCRIPTS_DIR`` - scripted sequences (default: ``scripts`` next to
  this file).
* ``MODEL_STUB_UPSTREAM_URL`` - OpenAI-compatible base URL used when recording
  (default ``https://api.openai.com/v1``).
* ``MODEL_STUB_HOST`` / ``MODEL_STUB_PORT`` - listen address (``0.0.0.0:8080``).
"""

from __future__ import annotations

import os
import sys

from tools.model_stub.guard import StartupRefusedError, assert_development_runtime

EXIT_CONFIG = 2
EXIT_REFUSED = 3


def main() -> int:
    # Before importing the server: application settings reject production at import time.
    try:
        assert_development_runtime(os.environ)
    except StartupRefusedError as exc:
        print(f"model-stub: refusing to start: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    from tools.model_stub.server import ConfigError, ModelStub, make_server, settings_from_env  # noqa: PLC0415

    try:
        settings = settings_from_env(os.environ)
        port = int(os.environ.get("MODEL_STUB_PORT", "8080"))
    except StartupRefusedError as exc:
        print(f"model-stub: refusing to start: {exc}", file=sys.stderr)
        return EXIT_REFUSED
    except (ConfigError, ValueError) as exc:
        print(f"model-stub: invalid configuration: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    host = os.environ.get("MODEL_STUB_HOST", "0.0.0.0")
    server = make_server(ModelStub(settings), host, port)
    print(
        f"model-stub: {settings.mode} mode, cassettes {settings.cassette_dir}, scripts {settings.scripts_dir}, "
        f"listening on {host}:{port}",
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

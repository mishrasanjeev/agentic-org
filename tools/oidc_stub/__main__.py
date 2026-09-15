# SPDX-License-Identifier: Apache-2.0
"""Run the development OIDC stub: ``python -m tools.oidc_stub``.

Environment:

* ``AGENTICORG_ENV`` - must be ``development``, ``local`` or ``test``; any
  production-like value in the usual environment variables refuses to start.
* ``OIDC_STUB_PUBLIC_URL`` - the issuer and the browser-facing base URL
  (default ``http://127.0.0.1:<port>``).
* ``OIDC_STUB_INTERNAL_URL`` - base URL other containers use for the token,
  userinfo and JWKS endpoints (default: the public URL).
* ``OIDC_STUB_CONFIG`` - users and clients JSON (default: ``config.dev.json``
  next to this file).
* ``OIDC_STUB_SIGNING_KEY_FILE`` - optional RSA private key (PEM); without it a
  new key is generated at start, so tokens do not survive a restart.
* ``OIDC_STUB_HOST`` / ``OIDC_STUB_PORT`` - listen address (``0.0.0.0:9400``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from tools.oidc_stub.server import (
    ConfigError,
    OIDCStub,
    SigningKey,
    StartupRefusedError,
    assert_development_runtime,
    load_config,
    make_server,
)

DEFAULT_CONFIG = Path(__file__).with_name("config.dev.json")
EXIT_CONFIG = 2
EXIT_REFUSED = 3


def _base_url(value: str, name: str) -> str:
    parts = urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc or parts.query or parts.fragment:
        raise ConfigError(f"{name} must be an absolute http(s) base URL, got {value!r}")
    return value.rstrip("/")


def main() -> int:
    try:
        runtime = assert_development_runtime(os.environ)
    except StartupRefusedError as exc:
        print(f"oidc-stub: refusing to start: {exc}", file=sys.stderr)
        return EXIT_REFUSED

    try:
        port = int(os.environ.get("OIDC_STUB_PORT", "9400"))
        host = os.environ.get("OIDC_STUB_HOST", "0.0.0.0")
        public_default = f"http://127.0.0.1:{port}"
        public_url = _base_url(os.environ.get("OIDC_STUB_PUBLIC_URL", public_default), "OIDC_STUB_PUBLIC_URL")
        internal_url = _base_url(os.environ.get("OIDC_STUB_INTERNAL_URL", public_url), "OIDC_STUB_INTERNAL_URL")
        config = load_config(Path(os.environ.get("OIDC_STUB_CONFIG", str(DEFAULT_CONFIG))))
        key_file = os.environ.get("OIDC_STUB_SIGNING_KEY_FILE", "")
        key = SigningKey.from_pem_file(Path(key_file)) if key_file else SigningKey.generate()
    except (ConfigError, ValueError) as exc:
        print(f"oidc-stub: invalid configuration: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    stub = OIDCStub(config, key, public_url=public_url, internal_url=internal_url)
    server = make_server(stub, host, port)
    print(
        f"oidc-stub: {runtime} runtime, issuer {stub.issuer}, {len(config.users)} users, "
        f"{len(config.clients)} clients, listening on {host}:{port}",
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

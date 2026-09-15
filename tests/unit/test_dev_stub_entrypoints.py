# SPDX-License-Identifier: Apache-2.0
"""Entry points of the development stubs: configuration errors and a clean serve/stop cycle."""

from __future__ import annotations

from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from tools.model_stub import __main__ as model_main
from tools.model_stub import server as model_server
from tools.oidc_stub import __main__ as oidc_main
from tools.oidc_stub import server as oidc_server


class InterruptedServer:
    """Stands in for ThreadingHTTPServer: serving stops as if Ctrl+C was pressed."""

    def __init__(self) -> None:
        self.closed = False
        self.bound: tuple[str, int] | None = None

    def serve_forever(self) -> None:
        raise KeyboardInterrupt

    def server_close(self) -> None:
        self.closed = True


def _clear_env(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    for name in names:
        monkeypatch.delenv(name, raising=False)


# ── OIDC stub ───────────────────────────────────────────────────────────────


def test_oidc_main_serves_until_interrupted_and_closes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fake = InterruptedServer()

    def make_server(stub: oidc_server.OIDCStub, host: str, port: int) -> InterruptedServer:
        fake.bound = (host, port)
        assert stub.issuer == "http://127.0.0.1:58391"
        assert stub.internal_url == "http://oidc-stub:9400"
        return fake

    key_file = tmp_path / "signing-key.pem"
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    key_file.write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    _clear_env(monkeypatch, "APP_ENV", "ENVIRONMENT", "ENV", "NODE_ENV", "OIDC_STUB_CONFIG")
    monkeypatch.setattr(oidc_main, "make_server", make_server)
    monkeypatch.setenv("AGENTICORG_ENV", "development")
    monkeypatch.setenv("OIDC_STUB_PORT", "58391")
    monkeypatch.setenv("OIDC_STUB_INTERNAL_URL", "http://oidc-stub:9400/")
    monkeypatch.setenv("OIDC_STUB_SIGNING_KEY_FILE", str(key_file))

    assert oidc_main.main() == 0
    assert fake.closed and fake.bound == ("0.0.0.0", 58391)
    err = capsys.readouterr().err
    assert "issuer http://127.0.0.1:58391, 2 users" in err


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("OIDC_STUB_PUBLIC_URL", "ftp://127.0.0.1:9400"),
        ("OIDC_STUB_INTERNAL_URL", "http://oidc-stub:9400/?x=1"),
        ("OIDC_STUB_PORT", "not-a-port"),
        ("OIDC_STUB_CONFIG", "/definitely/not/here.json"),
        ("OIDC_STUB_SIGNING_KEY_FILE", "/definitely/not/here.pem"),
    ],
)
def test_oidc_main_rejects_invalid_configuration_without_serving(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], name: str, value: str
) -> None:
    def no_server(*args: object) -> None:
        raise AssertionError("must not serve with invalid configuration")

    _clear_env(monkeypatch, "APP_ENV", "ENVIRONMENT", "ENV", "NODE_ENV", "OIDC_STUB_PUBLIC_URL",
               "OIDC_STUB_INTERNAL_URL", "OIDC_STUB_PORT", "OIDC_STUB_CONFIG", "OIDC_STUB_SIGNING_KEY_FILE")
    monkeypatch.setattr(oidc_main, "make_server", no_server)
    monkeypatch.setenv("AGENTICORG_ENV", "test")
    monkeypatch.setenv(name, value)
    assert oidc_main.main() == oidc_main.EXIT_CONFIG
    assert "invalid configuration" in capsys.readouterr().err


def test_oidc_signing_key_must_be_rsa(tmp_path: Path) -> None:
    from cryptography.hazmat.primitives.asymmetric import ec

    pem = tmp_path / "ec.pem"
    pem.write_bytes(
        ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
    )
    with pytest.raises(oidc_server.ConfigError, match="not an RSA private key"):
        oidc_server.SigningKey.from_pem_file(pem)


# ── Model stub ──────────────────────────────────────────────────────────────


def test_model_main_serves_until_interrupted_and_closes(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    fake = InterruptedServer()

    def make_server(stub: model_server.ModelStub, host: str, port: int) -> InterruptedServer:
        fake.bound = (host, port)
        assert stub.settings.mode == "replay"
        return fake

    _clear_env(monkeypatch, "APP_ENV", "ENVIRONMENT", "ENV", "NODE_ENV", "MODEL_STUB_MODE", "MODEL_STUB_SCRIPTS_DIR")
    monkeypatch.setattr(model_server, "make_server", make_server)
    monkeypatch.setenv("AGENTICORG_ENV", "development")
    monkeypatch.setenv("MODEL_STUB_CASSETTE_DIR", str(tmp_path))
    monkeypatch.setenv("MODEL_STUB_PORT", "58392")
    monkeypatch.setenv("MODEL_STUB_HOST", "127.0.0.1")

    assert model_main.main() == 0
    assert fake.closed and fake.bound == ("127.0.0.1", 58392)
    assert "replay mode" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("env", "code"),
    [
        ({"MODEL_STUB_PORT": "eighty"}, model_main.EXIT_CONFIG),
        ({"MODEL_STUB_MODE": "live"}, model_main.EXIT_CONFIG),
        ({"MODEL_STUB_SCRIPTS_DIR": "/definitely/not/here"}, model_main.EXIT_CONFIG),
    ],
)
def test_model_main_rejects_invalid_configuration_without_serving(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, env: dict[str, str], code: int
) -> None:
    def no_server(*args: object) -> None:
        raise AssertionError("must not serve with invalid configuration")

    _clear_env(monkeypatch, "APP_ENV", "ENVIRONMENT", "ENV", "NODE_ENV", "MODEL_STUB_MODE", "MODEL_STUB_SCRIPTS_DIR",
               "MODEL_STUB_PORT", "MODEL_RECORD_API_KEY")
    monkeypatch.setattr(model_server, "make_server", no_server)
    monkeypatch.setenv("AGENTICORG_ENV", "test")
    monkeypatch.setenv("MODEL_STUB_CASSETTE_DIR", str(tmp_path))
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    assert model_main.main() == code

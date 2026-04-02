"""VAPID key management for Web Push Protocol."""

from __future__ import annotations

import base64
import os

import structlog

_log = structlog.get_logger()


def get_vapid_keys() -> tuple[str, str]:
    """Return (public_key, private_key) from environment variables.

    Environment variables:
        VAPID_PUBLIC_KEY  — base64url-encoded P-256 public key
        VAPID_PRIVATE_KEY — base64url-encoded P-256 private key

    Raises:
        RuntimeError: If either key is missing from the environment.
    """
    public_key = os.environ.get("VAPID_PUBLIC_KEY", "")
    private_key = os.environ.get("VAPID_PRIVATE_KEY", "")
    if not public_key or not private_key:
        msg = (
            "VAPID keys not configured. Set VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY "
            "environment variables. Generate them with: "
            "python -c 'from core.push.vapid import generate_vapid_keys; print(generate_vapid_keys())'"
        )
        raise RuntimeError(msg)
    return public_key, private_key


def generate_vapid_keys() -> tuple[str, str]:
    """Generate a new VAPID key pair using EC P-256.

    Returns:
        Tuple of (public_key, private_key) as base64url-encoded strings
        (no padding), suitable for VAPID claims and subscription creation.

    Usage:
        Run once to generate keys, then store them in environment variables::

            python -c 'from core.push.vapid import generate_vapid_keys; \\
                pub, priv = generate_vapid_keys(); print(pub); print(priv)'
    """
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import (
        Encoding,
        PublicFormat,
    )

    private_key = ec.generate_private_key(ec.SECP256R1())

    # Export raw private key (32 bytes)
    private_numbers = private_key.private_numbers()
    private_bytes = private_numbers.private_value.to_bytes(32, byteorder="big")

    # Export uncompressed public key (65 bytes: 0x04 + x + y)
    public_bytes = private_key.public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint
    )

    # base64url encode without padding (as required by Web Push)
    public_b64 = base64.urlsafe_b64encode(public_bytes).rstrip(b"=").decode("ascii")
    private_b64 = base64.urlsafe_b64encode(private_bytes).rstrip(b"=").decode("ascii")

    _log.info("vapid_keys_generated", public_key_prefix=public_b64[:12])
    return public_b64, private_b64

"""HMAC-SHA256 signer for audit log tamper detection."""
from __future__ import annotations
import hashlib, hmac, json
from typing import Any
from core.config import settings

def sign(data: dict[str, Any]) -> str:
    payload = json.dumps(data, sort_keys=True, default=str)
    return hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()

def verify(data: dict[str, Any], signature: str) -> bool:
    return hmac.compare_digest(sign(data), signature)

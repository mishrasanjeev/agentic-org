# SPDX-License-Identifier: Apache-2.0
"""Standalone A2A HTTP+JSON buyer; imports no AgenticOrg application code."""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from urllib.error import HTTPError
from urllib.parse import urlparse
from urllib.request import Request, urlopen


def _request(base_url: str, path: str, *, token: str | None = None, question: str | None = None) -> tuple[int, dict]:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("A2A base URL must be an HTTP or HTTPS origin")
    headers = {"Accept": "application/a2a+json", "A2A-Version": "1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if question is not None:
        headers["Content-Type"] = "application/a2a+json"
        data = json.dumps({"message": {
            "messageId": str(uuid.uuid4()), "role": "ROLE_USER",
            "parts": [{"text": question}],
        }}).encode("utf-8")
    request = Request(base_url.rstrip("/") + path, data=data, headers=headers)  # noqa: S310
    try:
        with urlopen(request, timeout=15) as response:  # noqa: S310
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, {}


def run(base_url: str, token: str, *, expect_denied: bool = False) -> None:
    if expect_denied:
        status, _ = _request(base_url, "/api/v1/a2a/extendedAgentCard", token=token)
        if status != 401:
            raise RuntimeError(f"revoked buyer credential was not denied: HTTP {status}")
        print("Revocation: external buyer credential rejected (HTTP 401)")
        return

    status, public_card = _request(base_url, "/.well-known/agent-card.json")
    interfaces = public_card.get("supportedInterfaces", [])
    if status != 200 or not any(
        item.get("protocolBinding") == "HTTP+JSON" and item.get("protocolVersion") == "1.0"
        for item in interfaces
    ):
        raise RuntimeError("A2A v1 HTTP+JSON Agent Card was not available")
    status, seller_card = _request(base_url, "/api/v1/a2a/extendedAgentCard", token=token)
    if status != 200 or not any(skill.get("id") == "seller_commerce_query" for skill in seller_card.get("skills", [])):
        raise RuntimeError(f"seller-specific Agent Card unavailable: HTTP {status}")
    print(f"Seller Agent Card: {seller_card['name']}")

    for question, expected in (
        ("Show me your product catalogue", "answered"),
        ("Tell me about Canvas Tote", "answered"),
        ("Buy two Canvas Tote now", "refused"),
    ):
        status, response = _request(base_url, "/api/v1/a2a/message:send", token=token, question=question)
        message = response.get("message", {})
        metadata = message.get("metadata", {})
        if status != 200 or metadata.get("status") != expected:
            raise RuntimeError(f"A2A response did not meet the expected boundary: HTTP {status}")
        if metadata.get("allowedToExecute") is not False or metadata.get("nonAuthoritativeForTransaction") is not True:
            raise RuntimeError("A2A response lost its non-execution boundary")
        answer = " ".join(part.get("text", "") for part in message.get("parts", []))
        catalog_names = ("Canvas Tote", "Ceramic Mug", "Pocket Notebook")
        if question.startswith("Show") and not all(name in answer for name in catalog_names):
            raise RuntimeError("synthetic catalogue did not include all three products")
        if expected == "answered" and "synthetic local demo" not in metadata.get("sourceLabel", ""):
            raise RuntimeError("synthetic provenance was not disclosed to the buyer")
        print(f"Buyer: {question}")
        print(f"Seller ({expected}): {answer}")
        print(f"  {metadata.get('sourceLabel', '')}; {metadata.get('freshnessLabel', '')}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a real A2A v1 HTTP+JSON request from an external buyer process")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--expect-denied", action="store_true")
    args = parser.parse_args()
    token = os.getenv("A2A_BUYER_TOKEN", "")
    if not token:
        parser.error("A2A_BUYER_TOKEN must be supplied through the buyer process environment")
    try:
        run(args.base_url, token, expect_denied=args.expect_denied)
    except (RuntimeError, OSError, ValueError) as exc:
        print(f"A2A buyer demo failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

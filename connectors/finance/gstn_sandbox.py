"""GSTN Sandbox connector — for testing against Adaequare's sandbox environment.

Adaequare provides a sandbox GSP environment with test GSTINs for
development and integration testing before going live.

Usage:
    connector = GstnSandboxConnector(config={
        "username": "your-sandbox-user",
        "password": "your-sandbox-pass",
        "api_key": "your-sandbox-aspid",
    })
"""

from __future__ import annotations

from typing import Any

from connectors.finance.gstn import GstnConnector

# Adaequare sandbox environment
SANDBOX_BASE_URL = "https://gsp.adaequare.com/test/enriched/gsp"

# Test GSTINs available in Adaequare sandbox
SANDBOX_GSTINS = [
    "01AADCB2230M2ZR",  # State 01 — Jammu & Kashmir
    "29AADCB2230M1ZT",  # State 29 — Karnataka
    "07AADCB2230M1ZP",  # State 07 — Delhi
    "27AADCB2230M1ZV",  # State 27 — Maharashtra
    "33AADCB2230M1ZR",  # State 33 — Tamil Nadu
    "06AADCB2230M1ZQ",  # State 06 — Haryana
]

# Default sandbox GSTIN (Karnataka)
DEFAULT_SANDBOX_GSTIN = "29AADCB2230M1ZT"

# Test return periods
SANDBOX_RETURN_PERIODS = [
    "032026",  # March 2026
    "022026",  # February 2026
    "012026",  # January 2026
]

# Sample GSTR-1 test data for sandbox
SAMPLE_GSTR1_B2B = {
    "gstin": DEFAULT_SANDBOX_GSTIN,
    "return_period": "032026",
    "b2b": [
        {
            "ctin": "01AADCB2230M2ZR",
            "inv": [
                {
                    "inum": "TEST-INV-001",
                    "idt": "28-03-2026",
                    "val": 118000,
                    "pos": "29",
                    "rchrg": "N",
                    "itms": [
                        {
                            "num": 1,
                            "itm_det": {
                                "txval": 100000,
                                "rt": 18,
                                "iamt": 18000,
                                "camt": 0,
                                "samt": 0,
                                "csamt": 0,
                            },
                        }
                    ],
                }
            ],
        }
    ],
}


class GstnSandboxConnector(GstnConnector):
    """GSTN connector pre-configured for Adaequare sandbox environment."""

    name = "gstn_sandbox"
    base_url = SANDBOX_BASE_URL

    def __init__(self, config: dict[str, Any] | None = None):
        config = config or {}
        config.setdefault("gstin", DEFAULT_SANDBOX_GSTIN)
        super().__init__(config)

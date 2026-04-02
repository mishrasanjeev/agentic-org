"""CLI for the AgenticOrg Tally Bridge agent.

Usage:
    agenticorg-bridge start --cloud-url wss://app.agenticorg.ai/api/v1/ws/bridge \\
                            --bridge-id <id> --bridge-token <token>
    agenticorg-bridge register --api-key <key> --tenant-id <id>
    agenticorg-bridge status
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agenticorg-bridge",
        description="AgenticOrg Tally Bridge — tunnel local Tally to the cloud",
    )
    subparsers = parser.add_subparsers(dest="command")

    # start
    start_p = subparsers.add_parser("start", help="Start the bridge agent")
    start_p.add_argument("--cloud-url", required=True, help="WebSocket URL (wss://...)")
    start_p.add_argument("--bridge-id", required=True, help="Bridge ID from registration")
    start_p.add_argument("--bridge-token", required=True, help="Bridge auth token")
    start_p.add_argument("--tally-host", default="localhost", help="Tally host (default: localhost)")
    start_p.add_argument("--tally-port", type=int, default=9000, help="Tally port (default: 9000)")

    # register
    reg_p = subparsers.add_parser("register", help="Register bridge with cloud platform")
    reg_p.add_argument("--api-key", required=True, help="AgenticOrg API key")
    reg_p.add_argument("--tenant-id", required=True, help="Tenant ID")
    reg_p.add_argument(
        "--base-url",
        default="https://app.agenticorg.ai",
        help="API base URL",
    )
    reg_p.add_argument("--tally-port", type=int, default=9000, help="Local Tally port")

    # status
    status_p = subparsers.add_parser("status", help="Check bridge and Tally status")
    status_p.add_argument("--tally-host", default="localhost")
    status_p.add_argument("--tally-port", type=int, default=9000)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(0)

    if args.command == "start":
        _handle_start(args)
    elif args.command == "register":
        _handle_register(args)
    elif args.command == "status":
        _handle_status(args)


def _handle_start(args: argparse.Namespace) -> None:
    from bridge.tally_bridge import TallyBridge

    bridge = TallyBridge(
        cloud_url=args.cloud_url,
        bridge_id=args.bridge_id,
        bridge_token=args.bridge_token,
        tally_host=args.tally_host,
        tally_port=args.tally_port,
    )

    print(f"Starting Tally Bridge ({args.bridge_id})...")
    print(f"  Cloud:  {args.cloud_url}")
    print(f"  Tally:  http://{args.tally_host}:{args.tally_port}")
    print("  Press Ctrl+C to stop.\n")

    try:
        asyncio.run(bridge.start())
    except KeyboardInterrupt:
        print("\nBridge stopped.")


def _handle_register(args: argparse.Namespace) -> None:
    url = f"{args.base_url.rstrip('/')}/api/v1/bridge/register"
    headers = {"Authorization": f"Bearer {args.api_key}"}
    payload = {
        "tenant_id": args.tenant_id,
        "connector_type": "tally",
        "tally_port": args.tally_port,
    }

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        print("Bridge registered successfully!\n")
        print(f"  Bridge ID:    {data['bridge_id']}")
        print(f"  Bridge Token: {data['bridge_token']}")
        print("\nSave these credentials. Start the bridge with:")
        print("  agenticorg-bridge start \\")
        print("    --cloud-url wss://app.agenticorg.ai/api/v1/ws/bridge \\")
        print(f"    --bridge-id {data['bridge_id']} \\")
        print(f"    --bridge-token {data['bridge_token']}")
    except httpx.HTTPStatusError as exc:
        print(f"Registration failed: {exc.response.status_code} — {exc.response.text}", file=sys.stderr)
        sys.exit(1)
    except httpx.ConnectError:
        print(f"Could not connect to {url}", file=sys.stderr)
        sys.exit(1)


def _handle_status(args: argparse.Namespace) -> None:
    tally_url = f"http://{args.tally_host}:{args.tally_port}"
    health_xml = (
        '<?xml version="1.0" encoding="utf-8"?>'
        "<ENVELOPE><HEADER><VERSION>1</VERSION>"
        "<TALLYREQUEST>Export</TALLYREQUEST><TYPE>Data</TYPE>"
        "<ID>List of Companies</ID></HEADER><BODY/></ENVELOPE>"
    )

    print(f"Checking Tally at {tally_url}...")
    try:
        resp = httpx.post(
            tally_url,
            content=health_xml.encode("utf-8"),
            headers={"Content-Type": "application/xml"},
            timeout=5,
        )
        if resp.status_code == 200:
            print("  Tally: REACHABLE (HTTP 200)")
            if "<COMPANY" in resp.text or "<ENVELOPE" in resp.text:
                print("  Protocol: XML/TDL responding correctly")
            else:
                print("  Warning: Response doesn't look like Tally XML")
        else:
            print(f"  Tally: UNEXPECTED STATUS ({resp.status_code})")
    except httpx.ConnectError:
        print("  Tally: UNREACHABLE — is Tally running?")
    except Exception as exc:
        print(f"  Tally: ERROR — {exc}")


if __name__ == "__main__":
    main()

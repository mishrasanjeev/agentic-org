"""AgenticOrg CLI — manage AI agents from the terminal.

Usage:
    agenticorg agents list
    agenticorg agents list --domain finance
    agenticorg agents run ap_processor --input '{"invoice_id": "INV-001"}'
    agenticorg agents get <agent-id>
    agenticorg sop parse --file invoice_sop.pdf --domain finance
    agenticorg sop parse --text "Step 1: Receive invoice..."
    agenticorg a2a card
    agenticorg mcp tools
"""

from __future__ import annotations

import argparse
import json
import os
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="agenticorg",
        description="AgenticOrg CLI — manage AI agents from the terminal",
    )
    parser.add_argument("--api-key", help="API key (or set AGENTICORG_API_KEY env var)")
    parser.add_argument("--base-url", help="Base URL (default: https://app.agenticorg.ai)")

    subparsers = parser.add_subparsers(dest="command")

    # agents
    agents_parser = subparsers.add_parser("agents", help="Manage agents")
    agents_sub = agents_parser.add_subparsers(dest="action")

    list_p = agents_sub.add_parser("list", help="List agents")
    list_p.add_argument("--domain", help="Filter by domain")

    get_p = agents_sub.add_parser("get", help="Get agent details")
    get_p.add_argument("agent_id", help="Agent UUID")

    run_p = agents_sub.add_parser("run", help="Run an agent")
    run_p.add_argument("agent_type", help="Agent type or UUID")
    run_p.add_argument("--input", dest="input_json", help="JSON input data")
    run_p.add_argument("--action", default="process", help="Action (default: process)")

    # sop
    sop_parser = subparsers.add_parser("sop", help="Create agent from SOP")
    sop_sub = sop_parser.add_subparsers(dest="action")

    parse_p = sop_sub.add_parser("parse", help="Parse SOP document")
    parse_p.add_argument("--file", help="Path to PDF/markdown file")
    parse_p.add_argument("--text", help="SOP text (inline)")
    parse_p.add_argument("--domain", help="Domain hint")

    # a2a
    a2a_parser = subparsers.add_parser("a2a", help="A2A protocol")
    a2a_sub = a2a_parser.add_subparsers(dest="action")
    a2a_sub.add_parser("card", help="Get agent discovery card")
    a2a_sub.add_parser("agents", help="List A2A agents")

    # mcp
    mcp_parser = subparsers.add_parser("mcp", help="MCP protocol")
    mcp_sub = mcp_parser.add_subparsers(dest="action")
    mcp_sub.add_parser("tools", help="List MCP tools")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    # Build client
    from agenticorg import AgenticOrg

    try:
        client = AgenticOrg(
            api_key=args.api_key or os.getenv("AGENTICORG_API_KEY"),
            base_url=args.base_url,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.command == "agents":
            _handle_agents(client, args)
        elif args.command == "sop":
            _handle_sop(client, args)
        elif args.command == "a2a":
            _handle_a2a(client, args)
        elif args.command == "mcp":
            _handle_mcp(client, args)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


def _handle_agents(client, args):
    if args.action == "list":
        agents = client.agents.list(domain=args.domain)
        for a in agents:
            name = a.get("employee_name") or a.get("name", "")
            agent_type = a.get("agent_type", "")
            status = a.get("status", "")
            # Agent IDs truncated — no sensitive data exposed
            agent_id_short = str(a.get("id", ""))[:8]
            print(f"  {agent_id_short + '...':<12} {name:<25} {agent_type:<20} {status}")
        print(f"\n{len(agents)} agents")

    elif args.action == "get":
        agent = client.agents.get(args.agent_id)
        print(json.dumps(agent, indent=2, default=str))

    elif args.action == "run":
        inputs = json.loads(args.input_json) if args.input_json else {}
        result = client.agents.run(args.agent_type, action=args.action, inputs=inputs)
        print(json.dumps(result, indent=2, default=str))


def _handle_sop(client, args):
    if args.action == "parse":
        if args.file:
            result = client.sop.upload(args.file, domain_hint=args.domain or "")
        elif args.text:
            result = client.sop.parse_text(args.text, domain_hint=args.domain or "")
        else:
            print("Error: --file or --text required", file=sys.stderr)
            sys.exit(1)
        print(json.dumps(result, indent=2, default=str))


def _handle_a2a(client, args):
    if args.action == "card":
        card = client.a2a.agent_card()
        print(json.dumps(card, indent=2, default=str))
    elif args.action == "agents":
        agents = client.a2a.agents()
        for a in agents:
            print(f"  {a['id']:<25} {a['name']:<25} {a['domain']}")


def _handle_mcp(client, args):
    if args.action == "tools":
        tools = client.mcp.tools()
        for t in tools:
            print(f"  {t['name']:<35} {t['description'][:60]}")
        print(f"\n{len(tools)} tools")


if __name__ == "__main__":
    main()

"""AgenticOrg CLI - launch agents, discover A2A/MCP, use KB, and run workflows.

Usage:
    agenticorg agents list
    agenticorg agents run commerce_sales_agent --action buyer_discovery_preview \
        --input '{"merchant_id": "merchant_demo"}'
    agenticorg agents generate "Create a contract intelligence agent using Confluence and Jira"
    agenticorg workflows generate "Review vendor renewal risk"
    agenticorg workflows run <workflow-id> --input '{"vendor_id": "V-100"}'
    agenticorg knowledge search "vendor renewal policy"
    agenticorg a2a card
    agenticorg mcp tools
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="agenticorg",
        description="AgenticOrg CLI - launch agents, discover tools, and run workflows",
    )
    parser.add_argument("--api-key", help="API key (or set AGENTICORG_API_KEY env var)")
    parser.add_argument("--base-url", help="Base URL (default: https://app.agenticorg.ai)")

    subparsers = parser.add_subparsers(dest="command")

    # agents
    agents_parser = subparsers.add_parser("agents", help="Manage agents")
    agents_sub = agents_parser.add_subparsers(dest="agents_action")

    list_p = agents_sub.add_parser("list", help="List agents")
    list_p.add_argument("--domain", help="Filter by domain")

    get_p = agents_sub.add_parser("get", help="Get agent details")
    get_p.add_argument("agent_id", help="Agent UUID")

    run_p = agents_sub.add_parser("run", help="Run an agent")
    run_p.add_argument("agent_type", help="Agent type or UUID")
    run_p.add_argument("--input", dest="input_json", help="JSON input data")
    run_p.add_argument("--action", dest="run_action", default="process", help="Action (default: process)")

    gen_p = agents_sub.add_parser("generate", help="Generate an agent from a description")
    gen_p.add_argument("description", help="Plain-English agent description")
    gen_p.add_argument("--deploy", action="store_true", help="Deploy top suggestion as a shadow agent")
    gen_p.add_argument("--company-id", help="Company UUID for deployment")

    # connectors
    connectors_parser = subparsers.add_parser("connectors", help="Manage connectors")
    connectors_sub = connectors_parser.add_subparsers(dest="connectors_action")
    connectors_list = connectors_sub.add_parser("list", help="List connectors")
    connectors_list.add_argument("--category", help="Filter by category")
    connectors_get = connectors_sub.add_parser("get", help="Get connector details")
    connectors_get.add_argument("connector_id", help="Connector ID")

    # sop
    sop_parser = subparsers.add_parser("sop", help="Create agent from SOP")
    sop_sub = sop_parser.add_subparsers(dest="sop_action")

    parse_p = sop_sub.add_parser("parse", help="Parse SOP document")
    parse_p.add_argument("--file", help="Path to PDF/markdown file")
    parse_p.add_argument("--text", help="SOP text (inline)")
    parse_p.add_argument("--domain", help="Domain hint")

    # workflows
    workflows_parser = subparsers.add_parser("workflows", help="Generate and run workflows")
    workflows_sub = workflows_parser.add_subparsers(dest="workflows_action")

    templates_p = workflows_sub.add_parser("templates", help="List workflow templates")
    templates_p.add_argument("--domain", help="Filter by domain")

    workflows_sub.add_parser("list", help="List workflows")

    wf_get_p = workflows_sub.add_parser("get", help="Get workflow details")
    wf_get_p.add_argument("workflow_id", help="Workflow ID")

    wf_gen_p = workflows_sub.add_parser("generate", help="Generate workflow from description")
    wf_gen_p.add_argument("description", help="Plain-English workflow description")
    wf_gen_p.add_argument("--deploy", action="store_true", help="Create active workflow from generated definition")

    wf_create_p = workflows_sub.add_parser("create", help="Create workflow from JSON definition")
    wf_create_p.add_argument("--name", required=True, help="Workflow name")
    wf_create_p.add_argument("--definition", help="Workflow definition JSON")
    wf_create_p.add_argument("--definition-file", help="Path to workflow definition JSON")
    wf_create_p.add_argument("--domain", help="Workflow domain")
    wf_create_p.add_argument("--description", help="Workflow description")
    wf_create_p.add_argument("--trigger-type", help="Trigger type")
    wf_create_p.add_argument("--company-id", help="Company UUID")

    wf_run_p = workflows_sub.add_parser("run", help="Run a workflow")
    wf_run_p.add_argument("workflow_id", help="Workflow ID")
    wf_run_p.add_argument("--input", dest="input_json", help="JSON trigger payload")

    wf_run_get_p = workflows_sub.add_parser("get-run", help="Get workflow run status")
    wf_run_get_p.add_argument("run_id", help="Workflow run ID")

    # knowledge
    knowledge_parser = subparsers.add_parser("knowledge", help="Search tenant knowledge base")
    knowledge_sub = knowledge_parser.add_subparsers(dest="knowledge_action")
    knowledge_search = knowledge_sub.add_parser("search", help="Search tenant knowledge base")
    knowledge_search.add_argument("query", help="Search query")
    knowledge_search.add_argument("--top-k", type=int, default=5, help="Number of results")

    # a2a
    a2a_parser = subparsers.add_parser("a2a", help="A2A protocol")
    a2a_sub = a2a_parser.add_subparsers(dest="a2a_action")
    a2a_sub.add_parser("card", help="Get agent discovery card")
    a2a_sub.add_parser("agents", help="List A2A agents")

    # mcp
    mcp_parser = subparsers.add_parser("mcp", help="MCP protocol")
    mcp_sub = mcp_parser.add_subparsers(dest="mcp_action")
    mcp_sub.add_parser("tools", help="List MCP tools")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    from agenticorg import AgenticOrg

    try:
        client = AgenticOrg(
            api_key=args.api_key or os.getenv("AGENTICORG_API_KEY"),
            base_url=args.base_url,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    try:
        if args.command == "agents":
            _handle_agents(client, args)
        elif args.command == "connectors":
            _handle_connectors(client, args)
        elif args.command == "sop":
            _handle_sop(client, args)
        elif args.command == "workflows":
            _handle_workflows(client, args)
        elif args.command == "knowledge":
            _handle_knowledge(client, args)
        elif args.command == "a2a":
            _handle_a2a(client, args)
        elif args.command == "mcp":
            _handle_mcp(client, args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        client.close()


def _handle_agents(client: Any, args: argparse.Namespace) -> None:
    if args.agents_action == "list":
        agents = client.agents.list(domain=args.domain)
        for agent in agents:
            name = agent.get("employee_name") or agent.get("name", "")
            agent_type = agent.get("agent_type", "")
            status = agent.get("status", "")
            agent_id_short = str(agent.get("id", ""))[:8]
            print(f"  {agent_id_short + '...':<12} {name:<25} {agent_type:<20} {status}")
        print(f"\n{len(agents)} agents")
    elif args.agents_action == "get":
        _print_json(client.agents.get(args.agent_id))
    elif args.agents_action == "run":
        inputs = _load_json(args.input_json, default={})
        result = client.agents.run(args.agent_type, action=args.run_action, inputs=inputs)
        _print_json(result)
    elif args.agents_action == "generate":
        _print_json(
            client.agents.generate(
                args.description,
                deploy=args.deploy,
                company_id=args.company_id,
            )
        )
    else:
        raise ValueError("Unknown agents action")


def _handle_connectors(client: Any, args: argparse.Namespace) -> None:
    if args.connectors_action == "list":
        _print_json(client.connectors.list(category=args.category))
    elif args.connectors_action == "get":
        _print_json(client.connectors.get(args.connector_id))
    else:
        raise ValueError("Unknown connectors action")


def _handle_sop(client: Any, args: argparse.Namespace) -> None:
    if args.sop_action == "parse":
        if args.file:
            result = client.sop.upload(args.file, domain_hint=args.domain or "")
        elif args.text:
            result = client.sop.parse_text(args.text, domain_hint=args.domain or "")
        else:
            print("Error: --file or --text required", file=sys.stderr)
            sys.exit(1)
        _print_json(result)
    else:
        raise ValueError("Unknown SOP action")


def _handle_workflows(client: Any, args: argparse.Namespace) -> None:
    if args.workflows_action == "templates":
        _print_json(client.workflows.templates(domain=args.domain))
    elif args.workflows_action == "list":
        _print_json(client.workflows.list())
    elif args.workflows_action == "get":
        _print_json(client.workflows.get(args.workflow_id))
    elif args.workflows_action == "generate":
        _print_json(client.workflows.generate(args.description, deploy=args.deploy))
    elif args.workflows_action == "create":
        definition = _load_workflow_definition(args.definition, args.definition_file)
        _print_json(
            client.workflows.create(
                name=args.name,
                definition=definition,
                domain=args.domain,
                description=args.description,
                trigger_type=args.trigger_type,
                company_id=args.company_id,
            )
        )
    elif args.workflows_action == "run":
        _print_json(
            client.workflows.run(
                args.workflow_id,
                payload=_load_json(args.input_json, default={}),
            )
        )
    elif args.workflows_action == "get-run":
        _print_json(client.workflows.get_run(args.run_id))
    else:
        raise ValueError("Unknown workflows action")


def _handle_knowledge(client: Any, args: argparse.Namespace) -> None:
    if args.knowledge_action == "search":
        _print_json(client.knowledge.search(args.query, top_k=args.top_k))
    else:
        raise ValueError("Unknown knowledge action")


def _handle_a2a(client: Any, args: argparse.Namespace) -> None:
    if args.a2a_action == "card":
        _print_json(client.a2a.agent_card())
    elif args.a2a_action == "agents":
        agents = client.a2a.agents()
        for agent in agents:
            print(f"  {agent['id']:<25} {agent['name']:<25} {agent['domain']}")
    else:
        raise ValueError("Unknown A2A action")


def _handle_mcp(client: Any, args: argparse.Namespace) -> None:
    if args.mcp_action == "tools":
        tools = client.mcp.tools()
        for tool in tools:
            print(f"  {tool['name']:<35} {tool['description'][:60]}")
        print(f"\n{len(tools)} tools")
    else:
        raise ValueError("Unknown MCP action")


def _load_json(raw: str | None, *, default: dict[str, Any]) -> dict[str, Any]:
    if not raw:
        return default
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("JSON input must be an object")
    return data


def _load_workflow_definition(raw: str | None, file_path: str | None) -> dict[str, Any]:
    if raw and file_path:
        raise ValueError("Use only one of --definition or --definition-file")
    if file_path:
        raw = Path(file_path).read_text(encoding="utf-8")
    if not raw:
        raise ValueError("--definition or --definition-file is required")
    return _load_json(raw, default={})


def _print_json(value: Any) -> None:
    if is_dataclass(value):
        value = asdict(value)
    print(json.dumps(value, indent=2, default=str))


if __name__ == "__main__":
    main()

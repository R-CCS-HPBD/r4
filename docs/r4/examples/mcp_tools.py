#!/usr/bin/env python3
"""MCP tools example.

This script demonstrates:
1. Listing available MCP tools
2. Invoking tools directly
3. Using chat with tool calling
4. Running an agent loop

Usage:
    python mcp_tools.py [--list] [--invoke TOOL] [--chat MESSAGE]

Environment:
    R4_API_KEY: Your API key
    R4_GATEWAY_URL: Gateway URL (optional)
"""

import argparse
import json
from r4 import Client


def list_tools(client: Client) -> None:
    """List all available MCP tools."""
    print("Available MCP Tools")
    print("=" * 50)

    tools = client.mcp.list_tools()

    if not tools:
        print("No tools available")
        return

    # Group by server
    by_server = {}
    for tool in tools:
        server = tool.server or "default"
        if server not in by_server:
            by_server[server] = []
        by_server[server].append(tool)

    for server, server_tools in by_server.items():
        print(f"\n[{server}]")
        for tool in server_tools:
            print(f"  {tool.name}")
            print(f"    {tool.description}")

            # Show parameters
            if tool.parameters.get("properties"):
                params = list(tool.parameters["properties"].keys())
                print(f"    Parameters: {', '.join(params)}")


def invoke_tool(client: Client, tool_name: str, args_json: str = None) -> None:
    """Invoke a specific tool."""
    print(f"Invoking tool: {tool_name}")
    print("-" * 40)

    # Parse arguments
    kwargs = {}
    if args_json:
        kwargs = json.loads(args_json)
        print(f"Arguments: {kwargs}")

    result = client.mcp.invoke(tool_name, **kwargs)

    if result.success:
        print(f"\n✓ Success")
        print(f"Result:\n{result.result}")
    else:
        print(f"\n✗ Failed")
        print(f"Error: {result.error}")


def chat_with_tools(client: Client, message: str, tools: list = None) -> None:
    """Chat with automatic tool calling."""
    print(f"Chat with tools: {message}")
    print("-" * 40)

    response = client.mcp.chat_with_tools(
        message,
        tools=tools,
        max_iterations=5,
    )

    print(f"\nResponse: {response.content}")


def run_agent(client: Client, task: str) -> None:
    """Run an agent to complete a task."""
    print(f"Agent task: {task}")
    print("-" * 40)

    result = client.mcp.agent_run(
        task,
        max_iterations=10,
    )

    print(f"\n✓ Agent completed")
    print(f"\nTool calls made: {len(result.tool_calls)}")
    for call in result.tool_calls:
        print(f"  - {call.tool_name}")

    print(f"\nFinal response:\n{result.final_response}")


def main():
    parser = argparse.ArgumentParser(description="MCP tools example")
    parser.add_argument("--list", action="store_true", help="List available tools")
    parser.add_argument("--invoke", metavar="TOOL", help="Invoke a specific tool")
    parser.add_argument("--args", default="{}", help="JSON arguments for tool invocation")
    parser.add_argument("--chat", metavar="MESSAGE", help="Chat with tool calling enabled")
    parser.add_argument("--tools", nargs="*", help="Specific tools to enable for chat")
    parser.add_argument("--agent", metavar="TASK", help="Run an agent to complete a task")

    args = parser.parse_args()

    client = Client()

    # Check health
    if not client.health():
        print("Service is not available")
        return

    if args.list:
        list_tools(client)
    elif args.invoke:
        invoke_tool(client, args.invoke, args.args)
    elif args.chat:
        chat_with_tools(client, args.chat, args.tools)
    elif args.agent:
        run_agent(client, args.agent)
    else:
        # Default: list tools
        list_tools(client)


if __name__ == "__main__":
    main()

"""
Interactive chat client: bridges the Canvas MCP server with the Anthropic API.

Usage:
  python clients/client_anthropic.py

Requires ANTHROPIC_API_KEY in .env
"""

import asyncio
import json
import os
import sys

import anthropic
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompts import SYSTEM_PROMPT, stamp_message  # noqa: E402

load_dotenv()



def mcp_tool_to_anthropic(tool) -> dict:
    """Convert an MCP tool definition to the Anthropic tool_use format."""
    return {
        "name": tool.name,
        "description": tool.description or "",
        "input_schema": tool.inputSchema,
    }


async def run_chat():
    server_params = StdioServerParameters(
        command="python",
        args=["server.py"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise RuntimeError("ANTHROPIC_API_KEY not set in .env")

    client = anthropic.Anthropic(api_key=api_key)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_tools = await session.list_tools()
            tools = [mcp_tool_to_anthropic(t) for t in mcp_tools.tools]

            print("Canvas Assistant ready. Ask anything about your coursework.")
            print("Type 'quit' to exit.\n")

            messages = []

            while True:
                try:
                    user_input = input("You: ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nGoodbye!")
                    break

                if user_input.lower() in ("quit", "exit", "q"):
                    print("Goodbye!")
                    break

                if not user_input:
                    continue

                messages.append({"role": "user", "content": stamp_message(user_input)})

                # Agentic loop: keep going until Claude stops calling tools
                while True:
                    response = client.messages.create(
                        model="claude-sonnet-4-6",
                        max_tokens=4096,
                        system=SYSTEM_PROMPT,
                        tools=tools,
                        messages=messages,
                    )

                    tool_calls = [b for b in response.content if b.type == "tool_use"]
                    text_blocks = [b for b in response.content if b.type == "text"]

                    if not tool_calls:
                        final_text = " ".join(b.text for b in text_blocks).strip()
                        print(f"\nAssistant: {final_text}\n")
                        messages.append({"role": "assistant", "content": response.content})
                        break

                    messages.append({"role": "assistant", "content": response.content})
                    tool_results = []

                    for tc in tool_calls:
                        print(f"  [calling {tc.name}...]")
                        try:
                            result = await session.call_tool(tc.name, tc.input)
                            content = result.content[0].text if result.content else "{}"
                        except Exception as e:
                            content = json.dumps({"error": str(e)})

                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tc.id,
                            "content": content,
                        })

                    messages.append({"role": "user", "content": tool_results})


if __name__ == "__main__":
    asyncio.run(run_chat())

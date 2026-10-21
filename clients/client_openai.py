"""
Interactive chat client using OpenAI GPT-4o.
Connects to the same Canvas MCP server.

Usage:
  python clients/client_openai.py

Requires OPENAI_API_KEY in .env
"""

import asyncio
import json
import os
import sys

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompts import SYSTEM_PROMPT, stamp_message  # noqa: E402

load_dotenv()



def mcp_tool_to_openai(tool) -> dict:
    """Convert an MCP tool definition to the OpenAI function tool format."""
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description or "",
            "parameters": tool.inputSchema,
        },
    }


async def run_chat():
    server_params = StdioServerParameters(
        command="python",
        args=["server.py"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY not set in .env")

    client = OpenAI(api_key=api_key)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_tools = await session.list_tools()
            tools = [mcp_tool_to_openai(t) for t in mcp_tools.tools]

            print("Canvas Assistant ready. Ask anything about your coursework.")
            print("Type 'quit' to exit.\n")

            messages = [{"role": "system", "content": SYSTEM_PROMPT}]

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

                # Agentic loop: keep going until the model stops calling tools
                while True:
                    response = client.chat.completions.create(
                        model="gpt-4o",
                        messages=messages,
                        tools=tools,
                    )

                    choice = response.choices[0]
                    message = choice.message

                    if not message.tool_calls:
                        print(f"\nAssistant: {message.content}\n")
                        messages.append({"role": "assistant", "content": message.content})
                        break

                    messages.append(message)

                    for tc in message.tool_calls:
                        print(f"  [calling {tc.function.name}...]")
                        try:
                            args = json.loads(tc.function.arguments)
                            result = await session.call_tool(tc.function.name, args)
                            content = result.content[0].text if result.content else "{}"
                        except Exception as e:
                            content = json.dumps({"error": str(e)})

                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": content,
                        })


if __name__ == "__main__":
    asyncio.run(run_chat())

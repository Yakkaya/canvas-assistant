"""
Interactive chat client using Google Gemini (free tier).
Connects to the same Canvas MCP server.

Usage:
  python clients/client_gemini.py

Get a free API key at: aistudio.google.com
Add GEMINI_API_KEY to .env
"""

import asyncio
import json
import os
import sys

from google import genai
from google.genai import types
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from prompts import SYSTEM_PROMPT, stamp_message  # noqa: E402

load_dotenv()


TYPE_MAP = {
    "string": types.Type.STRING,
    "integer": types.Type.INTEGER,
    "number": types.Type.NUMBER,
    "boolean": types.Type.BOOLEAN,
    "array": types.Type.ARRAY,
    "object": types.Type.OBJECT,
}


def mcp_tool_to_gemini(tool) -> types.FunctionDeclaration:
    """Convert an MCP tool to a Gemini FunctionDeclaration."""
    schema = tool.inputSchema or {}

    properties = {
        name: types.Schema(
            type=TYPE_MAP.get(prop.get("type", "string").lower(), types.Type.STRING),
            description=prop.get("description", ""),
        )
        for name, prop in schema.get("properties", {}).items()
    }

    params = types.Schema(
        type=types.Type.OBJECT,
        properties=properties,
        required=schema.get("required", []),
    )

    return types.FunctionDeclaration(
        name=tool.name,
        description=tool.description or "",
        parameters=params,
    )


async def run_chat():
    server_params = StdioServerParameters(
        command="python",
        args=["server.py"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in .env")

    client = genai.Client(api_key=api_key)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            mcp_tools = await session.list_tools()
            gemini_tool = types.Tool(
                function_declarations=[mcp_tool_to_gemini(t) for t in mcp_tools.tools]
            )

            config = types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                tools=[gemini_tool],
            )

            print("Canvas Assistant ready. Ask anything about your coursework.")
            print("Type 'quit' to exit.\n")

            chat = client.chats.create(model="gemini-2.5-flash-lite", config=config)

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

                response = chat.send_message(stamp_message(user_input))

                # Agentic loop: keep going until Gemini stops calling tools
                while True:
                    parts = response.candidates[0].content.parts
                    function_calls = [
                        p.function_call for p in parts
                        if p.function_call and p.function_call.name
                    ]

                    if not function_calls:
                        text = " ".join(p.text for p in parts if p.text).strip()
                        print(f"\nAssistant: {text}\n")
                        break

                    function_responses = []
                    for fc in function_calls:
                        print(f"  [calling {fc.name}...]")
                        try:
                            args = dict(fc.args)
                            result = await session.call_tool(fc.name, args)
                            content = result.content[0].text if result.content else "{}"
                        except Exception as e:
                            content = json.dumps({"error": str(e)})

                        function_responses.append(
                            types.Part.from_function_response(
                                name=fc.name,
                                response={"result": content},
                            )
                        )

                    response = chat.send_message(function_responses)


if __name__ == "__main__":
    asyncio.run(run_chat())

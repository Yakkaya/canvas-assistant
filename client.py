"""
Interactive chat client: bridges the Canvas MCP server with the Anthropic API.

Usage:
  python client.py

Requires ANTHROPIC_API_KEY in .env
"""

import asyncio
import json
import os
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

load_dotenv()

SYSTEM_PROMPT = f"""You are a student assistant for a Cal Poly student using Canvas LMS.
You have access to tools that fetch the student's assignments, todos, and course information from Canvas.

Today's date: {datetime.now(timezone.utc).strftime("%A, %B %d, %Y")} (UTC / Pacific time is 8 hours behind)

Your role is to help the student prioritize their work and answer questions like:
- "What should I work on this week?"
- "Which course needs the most attention right now?"
- "Is it better to focus on my midterm or my project?"
- "What's due tomorrow?"

When using syllabus data (grading weights, late policies):
- If a confidence score is below 0.7, note that the data may not be fully accurate
- If no syllabus data exists for a course, mention that grading weights are unavailable for it
- Confidence of 0.85 = extracted from a table (reliable), 0.70 = regex match, 0.40 = keyword only

Be direct and practical. Students want actionable answers.
When you call tools, don't narrate every call — just use the data to give a good answer."""


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
        cwd=os.path.dirname(os.path.abspath(__file__)),
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

                messages.append({"role": "user", "content": user_input})

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
                        # Final text response
                        final_text = " ".join(b.text for b in text_blocks).strip()
                        print(f"\nAssistant: {final_text}\n")
                        messages.append({"role": "assistant", "content": response.content})
                        break

                    # Execute all tool calls
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

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
from datetime import datetime, timezone

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from openai import OpenAI

load_dotenv()

SYSTEM_PROMPT = f"""You are a student assistant for a Cal Poly student using Canvas LMS.
You have access to tools that fetch the student's assignments, todos, and course information from Canvas.

Today's date: {datetime.now(timezone.utc).strftime("%A, %B %d, %Y")} (UTC / Pacific time is 8 hours behind)

Your role is to help the student prioritize their work and answer questions like:
- "What should I work on this week?"
- "Which course needs the most attention right now?"
- "Is it better to focus on my midterm or my project?"
- "What's due tomorrow?"

IMPORTANT RULES:
- Always use your tools to look up data before answering. Do NOT tell the student to "check the syllabus themselves" — that's your job.
- When asked about late policies, call get_course_info for the relevant course and report what you find.
- When asked to prioritize, call get_upcoming_assignments and get_all_courses, then give a specific recommendation.
- Never refuse to give advice. Always make a best-effort recommendation with the data available.

When using syllabus data (grading weights, late policies):
- Confidence is rated 1–5 (5 = from Canvas directly or a structured PDF table, 1 = guessed).
- If a confidence score is 3 or below, note that the data may not be fully accurate.
- Always tell the student where the information came from, using plain language:
  - source "canvas_api" → say "from Canvas"
  - source "syllabus_html" → say "from the course syllabus"
  - source "syllabus_pdf" → say "from the syllabus PDF"
  - source "user_override" → say "based on your correction"
- If no syllabus data exists for a course, mention that grading weights are unavailable.

Be direct and practical. Students want actionable answers."""


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

                messages.append({"role": "user", "content": user_input})

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

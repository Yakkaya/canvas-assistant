"""
MCP server exposing Canvas student data as tools.

Tools:
  - get_upcoming_assignments(days): assignments due in next N days
  - get_todo_items(): current Canvas todo list
  - get_course_info(course_id): course details + syllabus data
  - get_all_courses(): all enrolled courses with grading breakdowns
  - refresh_canvas_data(): fetch fresh data from Canvas API
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from db import MongoStore
from tools import TOOL_SCHEMAS, execute_tool

load_dotenv()

STUDENT_ID = "self"

server = Server("canvas-assistant")
store = MongoStore()


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name=t["name"], description=t["description"], inputSchema=t["parameters"])
        for t in TOOL_SCHEMAS
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    return [TextContent(type="text", text=execute_tool(store, STUDENT_ID, name, arguments or {}))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())

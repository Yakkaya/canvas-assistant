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
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

import canvas_api
import parsers
from db import MongoStore

load_dotenv()

STUDENT_ID = "self"

server = Server("canvas-assistant")
store = MongoStore()


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_upcoming_assignments",
            description=(
                "Get assignments due within the next N days from the student's stored Canvas data. "
                "Returns due dates, points, category, and submission status."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days to look ahead (default 7)",
                        "default": 7,
                    }
                },
            },
        ),
        Tool(
            name="get_todo_items",
            description="Get the student's current Canvas todo list — assignments pending submission.",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="get_course_info",
            description=(
                "Get detailed info for a specific course including grading weights and late policy "
                "parsed from the syllabus. Includes confidence scores for each weight."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "course_id": {
                        "type": "integer",
                        "description": "The Canvas course ID",
                    }
                },
                "required": ["course_id"],
            },
        ),
        Tool(
            name="get_all_courses",
            description=(
                "List all courses the student is enrolled in, with grading breakdowns and assignment summaries."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="refresh_canvas_data",
            description=(
                "Fetch fresh todo and assignment data from the Canvas API and update the database. "
                "Use this if data seems stale or the student says something doesn't match Canvas."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "get_upcoming_assignments":
        days = int(arguments.get("days", 7))
        assignments = store.get_upcoming_assignments(STUDENT_ID, days)
        result = [
            {
                "id": a.id,
                "name": a.name,
                "course_id": a.course_id,
                "due_at": a.due_at.isoformat() if a.due_at else None,
                "points_possible": a.points_possible,
                "category": a.category.value if a.category else None,
                "has_submitted": a.has_submitted,
                "is_locked": a.is_locked,
                "html_url": a.html_url,
                "confidence": a.confidence,
            }
            for a in assignments
        ]
        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "get_todo_items":
        todos = store.get_todos(STUDENT_ID)
        clean = []
        for t in todos:
            item = {k: v for k, v in t.items() if k != "_id"}
            for k, v in item.items():
                if isinstance(v, datetime):
                    item[k] = v.isoformat()
            clean.append(item)
        return [TextContent(type="text", text=json.dumps(clean, indent=2))]

    elif name == "get_all_courses":
        data = store.load_student_data(STUDENT_ID)
        return [TextContent(type="text", text=json.dumps(data.to_dict(), indent=2))]

    elif name == "get_course_info":
        course_id = arguments.get("course_id")
        if not course_id:
            return [TextContent(type="text", text='{"error": "course_id is required"}')]

        course = store.get_course(int(course_id))
        if not course:
            return [TextContent(
                type="text",
                text=json.dumps({"error": f"Course {course_id} not found in database. Try refresh_canvas_data first."})
            )]

        result = {
            "id": course.id,
            "name": course.name,
            "code": course.code,
            "html_url": course.html_url,
            "has_syllabus": course.syllabus is not None,
        }

        if course.syllabus:
            s = course.syllabus
            result["grading_categories"] = [
                {
                    "name": gc.name,
                    "category": gc.assignment_category.value,
                    "weight": gc.weight,
                    "weight_pct": f"{gc.weight * 100:.0f}%",
                    "confidence": gc.confidence,
                    "source": gc.source.value,
                }
                for gc in s.grading_categories
            ]
            if s.late_policy:
                lp = s.late_policy
                result["late_policy"] = {
                    "allows_late": lp.allows_late,
                    "penalty_per_day": lp.penalty_per_day,
                    "max_days_late": lp.max_days_late,
                    "raw_text": lp.raw_text,
                    "confidence": lp.confidence,
                }

        return [TextContent(type="text", text=json.dumps(result, indent=2))]

    elif name == "refresh_canvas_data":
        try:
            raw = canvas_api.get_user_todo()
            data = parsers.load_and_parse_todos(raw)
            store.save_student(STUDENT_ID, os.getenv("CANVAS_TOKEN", ""), "Student")

            # Fetch and parse syllabus for each course
            syllabi_parsed = 0
            for course_id, course in data.courses.items():
                try:
                    course_data = canvas_api.get_course(course_id)
                    syllabus_html = course_data.get("syllabus_body") or ""
                    if syllabus_html.strip():
                        course.syllabus = parsers.parse_syllabus_html(syllabus_html, course_id)
                        syllabi_parsed += 1
                except Exception:
                    pass  # Skip courses we can't fetch

            store.save_student_data(STUDENT_ID, data)
            summary = {
                "status": "success",
                "courses_refreshed": len(data.courses),
                "syllabi_parsed": syllabi_parsed,
                "assignments_refreshed": len(data.assignments),
                "todos_refreshed": len(data.todo_items),
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            summary = {"status": "error", "message": str(e)}

        return [TextContent(type="text", text=json.dumps(summary, indent=2))]

    return [TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))]


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())

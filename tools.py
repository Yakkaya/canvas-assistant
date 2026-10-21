"""
Canvas assistant tools, shared by the MCP server (server.py) and the web
backend (web_server.py).

Every tool returns a JSON string. Datetimes are rendered in US Pacific time
with their UTC offset, and syllabus values carry their confidence and source
so the agent can report uncertainty and provenance.
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional

import canvas_api
import parsers
from db import MongoStore
from models import Assignment, Course
from prompts import PACIFIC


# JSON-schema tool definitions, shared by the MCP server and the eval harness
TOOL_SCHEMAS = [
    {
        "name": "get_upcoming_assignments",
        "description": (
            "Get assignments due within the next N days from the student's stored Canvas data. "
            "Returns due dates, points, category, and submission status."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "days": {
                    "type": "integer",
                    "description": "Number of days to look ahead (default 7)",
                }
            },
        },
    },
    {
        "name": "get_todo_items",
        "description": "Get the student's current Canvas todo list — assignments pending submission.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "get_course_info",
        "description": (
            "Get detailed info for a specific course including grading weights and late policy "
            "parsed from the syllabus. Includes confidence scores for each weight."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "course_id": {"type": "integer", "description": "The Canvas course ID"}
            },
            "required": ["course_id"],
        },
    },
    {
        "name": "get_all_courses",
        "description": (
            "List all courses the student is enrolled in, with grading breakdowns and assignment summaries."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "refresh_canvas_data",
        "description": (
            "Fetch fresh todo and assignment data from the Canvas API and update the database. "
            "Use this if data seems stale or the student says something doesn't match Canvas."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
]


def to_pacific(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)  # Stored values are UTC
    return dt.astimezone(PACIFIC).isoformat()


def assignment_summary(a: Assignment) -> dict:
    return {
        "id": a.id,
        "name": a.name,
        "course_id": a.course_id,
        "due_at": to_pacific(a.due_at),
        "points_possible": a.points_possible,
        "category": a.category.value if a.category else None,
        "has_submitted": a.has_submitted,
        "is_locked": a.is_locked,
        "canvas_link": a.html_url,
        "confidence": a.confidence,
        "source": a.source.value,
    }


def course_summary(course: Course) -> dict:
    result = {
        "id": course.id,
        "name": course.name,
        "code": course.code,
        "canvas_link": course.html_url,
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
                "source": lp.source.value,
            }
    return result


def refresh_canvas_data(store: MongoStore, student_id: str) -> dict:
    """Fetch todos, courses, assignments, and syllabi from Canvas and persist them."""
    raw = canvas_api.get_user_todo()
    data = parsers.load_and_parse_todos(raw)
    store.save_student(student_id, os.getenv("CANVAS_TOKEN", ""), "Student")

    for course_raw in canvas_api.get_courses():
        course_id = course_raw["id"]
        data.add_course(parsers.parse_course(course_raw))
        try:
            for a_raw in canvas_api.get_course_assignments(course_id):
                data.add_assignment(parsers.parse_assignment(a_raw))
        except Exception:
            pass

    parsers.enrich_assignment_categories(data)

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

    store.save_student_data(student_id, data)
    return {
        "status": "success",
        "courses_refreshed": len(data.courses),
        "syllabi_parsed": syllabi_parsed,
        "assignments_refreshed": len(data.assignments),
        "todos_refreshed": len(data.todo_items),
        "refreshed_at": to_pacific(datetime.now(timezone.utc)),
    }


def execute_tool(store: MongoStore, student_id: str, name: str, arguments: dict) -> str:
    """Execute a Canvas tool and return a JSON string result."""
    if name == "get_upcoming_assignments":
        days = int(arguments.get("days", 7))
        assignments = store.get_upcoming_assignments(student_id, days)
        return json.dumps([assignment_summary(a) for a in assignments], indent=2)

    elif name == "get_todo_items":
        clean = []
        for t in store.get_todos(student_id):
            item = {k: v for k, v in t.items() if k not in ("_id", "student_id")}
            if "html_url" in item:
                item["canvas_link"] = item.pop("html_url")
            for k, v in item.items():
                if isinstance(v, datetime):
                    item[k] = to_pacific(v)
            clean.append(item)
        return json.dumps(clean, indent=2)

    elif name == "get_all_courses":
        data = store.load_student_data(student_id)
        return json.dumps({
            "courses": [course_summary(c) for c in data.courses.values()],
            "assignments": [assignment_summary(a) for a in data.assignments.values()],
        }, indent=2)

    elif name == "get_course_info":
        course_id = arguments.get("course_id")
        if not course_id:
            return json.dumps({"error": "course_id is required"})
        course = store.get_course(int(course_id))
        if not course:
            return json.dumps({
                "error": f"Course {course_id} not found. Try refresh_canvas_data first."
            })
        return json.dumps(course_summary(course), indent=2)

    elif name == "refresh_canvas_data":
        try:
            return json.dumps(refresh_canvas_data(store, student_id))
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    return json.dumps({"error": f"Unknown tool: {name}"})

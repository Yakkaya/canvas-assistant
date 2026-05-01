"""
Web server for the Canvas Assistant chatbot UI.

Provides a /chat HTTP endpoint that the Tampermonkey widget calls.
Each session gets a persistent Gemini Chat object stored in memory.

Run with:
  uvicorn web_server:app --port 8000
"""

import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from google import genai
from google.genai import types
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
load_dotenv(os.path.join(_HERE, ".env"))

import canvas_api
import parsers
from db import MongoStore

STUDENT_ID = "self"
store = MongoStore()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("GEMINI_API_KEY not set in .env")

gemini_client = genai.Client(api_key=api_key)

# session_id -> Gemini Chat object (manages its own history internally)
sessions: dict[str, any] = {}

SYSTEM_PROMPT = f"""You are a student assistant for a Cal Poly student using Canvas LMS.
You have access to tools that fetch the student's assignments, todos, and course information from Canvas.

Today's date: {datetime.now(timezone.utc).strftime("%A, %B %d, %Y")} (UTC / Pacific time is 8 hours behind)

Your role is to help the student prioritize their work and answer questions like:
- "What should I work on this week?"
- "Which course needs the most attention right now?"
- "Is it better to focus on my midterm or my project?"
- "What's due tomorrow?"
- "Is it worth submitting late?"

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
- If sources differ across categories, note each one individually.
- If no late policy is found, say so and advise the student to check Canvas directly.

Be direct and practical. Students want actionable answers, not instructions to go look things up themselves."""

TYPE_MAP = {
    "string": types.Type.STRING,
    "integer": types.Type.INTEGER,
    "number": types.Type.NUMBER,
    "boolean": types.Type.BOOLEAN,
    "array": types.Type.ARRAY,
    "object": types.Type.OBJECT,
}

TOOL_DEFINITIONS = [
    types.FunctionDeclaration(
        name="get_upcoming_assignments",
        description=(
            "Get assignments due within the next N days from the student's stored Canvas data. "
            "Returns due dates, points, category, and submission status."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "days": types.Schema(
                    type=types.Type.INTEGER,
                    description="Number of days to look ahead (default 7)",
                )
            },
        ),
    ),
    types.FunctionDeclaration(
        name="get_todo_items",
        description="Get the student's current Canvas todo list — assignments pending submission.",
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name="get_course_info",
        description=(
            "Get detailed info for a specific course including grading weights and late policy "
            "parsed from the syllabus."
        ),
        parameters=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "course_id": types.Schema(
                    type=types.Type.INTEGER,
                    description="The Canvas course ID",
                )
            },
            required=["course_id"],
        ),
    ),
    types.FunctionDeclaration(
        name="get_all_courses",
        description="List all courses the student is enrolled in, with grading breakdowns and assignment summaries.",
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
    types.FunctionDeclaration(
        name="refresh_canvas_data",
        description=(
            "Fetch fresh todo and assignment data from the Canvas API and update the database. "
            "Use this if data seems stale or the student says something doesn't match Canvas."
        ),
        parameters=types.Schema(type=types.Type.OBJECT, properties={}),
    ),
]

CHAT_CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_PROMPT,
    tools=[types.Tool(function_declarations=TOOL_DEFINITIONS)],
)


def execute_tool(name: str, arguments: dict) -> str:
    """Execute a Canvas tool and return a JSON string result."""
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
        return json.dumps(result, indent=2)

    elif name == "get_todo_items":
        todos = store.get_todos(STUDENT_ID)
        clean = []
        for t in todos:
            item = {k: v for k, v in t.items() if k != "_id"}
            for k, v in item.items():
                if isinstance(v, datetime):
                    item[k] = v.isoformat()
            clean.append(item)
        return json.dumps(clean, indent=2)

    elif name == "get_all_courses":
        data = store.load_student_data(STUDENT_ID)
        return json.dumps(data.to_dict(), indent=2)

    elif name == "get_course_info":
        course_id = arguments.get("course_id")
        if not course_id:
            return json.dumps({"error": "course_id is required"})
        course = store.get_course(int(course_id))
        if not course:
            return json.dumps({
                "error": f"Course {course_id} not found. Try refresh_canvas_data first."
            })
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
        return json.dumps(result, indent=2)

    elif name == "refresh_canvas_data":
        try:
            # Fetch todo items (pending submissions)
            raw = canvas_api.get_user_todo()
            data = parsers.load_and_parse_todos(raw)
            store.save_student(STUDENT_ID, os.getenv("CANVAS_TOKEN", ""), "Student")

            # Fetch all enrolled courses and their upcoming assignments
            all_courses = canvas_api.get_courses()
            for course_raw in all_courses:
                course_id = course_raw["id"]
                course = parsers.parse_course(course_raw)
                data.add_course(course)

                try:
                    for a_raw in canvas_api.get_course_assignments(course_id):
                        assignment = parsers.parse_assignment(a_raw)
                        data.add_assignment(assignment)
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
                    pass
            store.save_student_data(STUDENT_ID, data)
            return json.dumps({
                "status": "success",
                "courses_refreshed": len(data.courses),
                "syllabi_parsed": syllabi_parsed,
                "assignments_refreshed": len(data.assignments),
                "todos_refreshed": len(data.todo_items),
                "refreshed_at": datetime.now(timezone.utc).isoformat(),
            })
        except Exception as e:
            return json.dumps({"status": "error", "message": str(e)})

    return json.dumps({"error": f"Unknown tool: {name}"})


def run_chat_turn(session_id: str, message) -> str:
    """Send a message and run the agentic tool loop. Returns final text reply."""
    if session_id not in sessions:
        sessions[session_id] = gemini_client.chats.create(
            model="gemini-2.5-flash-lite",
            config=CHAT_CONFIG,
        )

    chat_obj = sessions[session_id]
    response = chat_obj.send_message(message)

    while True:
        parts = response.candidates[0].content.parts
        function_calls = [
            p.function_call for p in parts
            if p.function_call and p.function_call.name
        ]

        if not function_calls:
            return " ".join(p.text for p in parts if p.text).strip()

        function_responses = []
        for fc in function_calls:
            try:
                result = execute_tool(fc.name, dict(fc.args))
            except Exception as e:
                result = json.dumps({"error": str(e)})

            function_responses.append(
                types.Part.from_function_response(
                    name=fc.name,
                    response={"result": result},
                )
            )

        response = chat_obj.send_message(function_responses)


async def manual_update(request: Request) -> JSONResponse:
    body = await request.json()
    update_type = body.get("update_type", "")
    course_id = body.get("course_id")
    data = body.get("data", {})

    valid_types = ("grading_weight", "late_policy", "assignment_category")
    if update_type not in valid_types:
        return JSONResponse(
            {"error": f"update_type must be one of: {', '.join(valid_types)}"},
            status_code=400,
        )
    if update_type in ("grading_weight", "late_policy") and not course_id:
        return JSONResponse({"error": "course_id is required"}, status_code=400)

    try:
        store.apply_manual_update(STUDENT_ID, update_type, course_id, data)
        return JSONResponse({"status": "ok"})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def chat(request: Request) -> JSONResponse:
    body = await request.json()
    message = (body.get("message") or "").strip()
    session_id = body.get("session_id") or str(uuid.uuid4())

    if not message:
        return JSONResponse({"error": "empty message"}, status_code=400)

    reply = await asyncio.to_thread(run_chat_turn, session_id, message)
    return JSONResponse({"reply": reply, "session_id": session_id})


app = Starlette(routes=[
    Route("/chat", chat, methods=["POST"]),
    Route("/manual-update", manual_update, methods=["POST"]),
])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "OPTIONS"],
    allow_headers=["*"],
)

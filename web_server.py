"""
Web server for the Canvas Assistant chatbot UI.

Provides a /chat HTTP endpoint that the Tampermonkey widget calls.
Each session gets a persistent Gemini Chat object stored in memory.

Every chat turn (message, tool calls with arguments and results, reply) is
appended as one JSON line to AGENT_LOG_PATH (default eval/logs/agent_turns.jsonl)
so agent behavior can be reviewed after the fact.

Run with:
  uvicorn web_server:app --port 8000
"""

import asyncio
import json
import os
import sys
import time
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

from db import MongoStore
from prompts import SYSTEM_PROMPT, stamp_message
from tools import execute_tool

STUDENT_ID = "self"
# Built on gemini-2.5-flash; that model is no longer available to new API keys
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
AGENT_LOG_PATH = os.getenv(
    "AGENT_LOG_PATH", os.path.join(_HERE, "eval", "logs", "agent_turns.jsonl")
)
store = MongoStore()

api_key = os.getenv("GEMINI_API_KEY")
if not api_key:
    raise RuntimeError("GEMINI_API_KEY not set in .env")

gemini_client = genai.Client(api_key=api_key)

# session_id -> Gemini Chat object (manages its own history internally)
sessions: dict[str, any] = {}

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


def log_turn(record: dict) -> None:
    """Append one chat turn to the agent log. Logging failures never break chat."""
    try:
        os.makedirs(os.path.dirname(AGENT_LOG_PATH), exist_ok=True)
        with open(AGENT_LOG_PATH, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")
    except OSError:
        pass


def add_usage(record: dict, response) -> None:
    """Accumulate billed tokens (thinking tokens are billed as output)."""
    u = getattr(response, "usage_metadata", None)
    if u:
        record["usage"]["input_tokens"] += u.prompt_token_count or 0
        record["usage"]["output_tokens"] += (u.candidates_token_count or 0) + (u.thoughts_token_count or 0)


def run_chat_turn(session_id: str, message: str) -> str:
    """Send a message and run the agentic tool loop. Returns final text reply."""
    new_session = session_id not in sessions
    if new_session:
        sessions[session_id] = gemini_client.chats.create(model=MODEL, config=CHAT_CONFIG)

    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": session_id,
        "new_session": new_session,
        "model": MODEL,
        "message": message,
        "tool_calls": [],
        "model_rounds": 0,
        "reply": None,
        "error": None,
        "usage": {"input_tokens": 0, "output_tokens": 0},
    }
    started = time.monotonic()

    chat_obj = sessions[session_id]
    try:
        response = chat_obj.send_message(stamp_message(message))
        add_usage(record, response)
        record["model_rounds"] = 1

        for _ in range(10):  # cap tool loop to prevent runaway calls
            candidate = response.candidates[0] if response.candidates else None
            parts = (candidate.content.parts if candidate and candidate.content else None) or []
            function_calls = [
                p.function_call for p in parts
                if p.function_call and p.function_call.name
            ]

            if not function_calls:
                text = " ".join(p.text for p in parts if p.text).strip()
                record["reply"] = text or "I wasn't able to generate a response. Please try again."
                return record["reply"]

            function_responses = []
            for fc in function_calls:
                args = dict(fc.args or {})
                try:
                    result = execute_tool(store, STUDENT_ID, fc.name, args)
                except Exception as e:
                    result = json.dumps({"error": str(e)})
                record["tool_calls"].append({
                    "round": record["model_rounds"],
                    "name": fc.name,
                    "args": args,
                    "result": result,
                })
                function_responses.append(
                    types.Part.from_function_response(
                        name=fc.name,
                        response={"result": result},
                    )
                )

            response = chat_obj.send_message(function_responses)
            add_usage(record, response)
            record["model_rounds"] += 1

        record["reply"] = "I wasn't able to complete the request. Please try again."
        record["error"] = "tool loop limit reached"
        return record["reply"]

    except Exception as e:
        record["error"] = repr(e)
        # Drop the broken session so the next request starts fresh
        sessions.pop(session_id, None)
        raise
    finally:
        record["duration_s"] = round(time.monotonic() - started, 2)
        log_turn(record)


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


async def form_data(request: Request) -> JSONResponse:
    """Return courses and assignments so the UI can populate dropdowns."""
    data = store.load_student_data(STUDENT_ID)
    courses = [
        {"id": c.id, "label": f"{c.code} — {c.name}"}
        for c in sorted(data.courses.values(), key=lambda c: c.code or "")
    ]
    assignments = [
        {
            "id": a.id,
            "label": f"{data.courses[a.course_id].code if a.course_id in data.courses else '?'}: {a.name}",
            "course_id": a.course_id,
        }
        for a in sorted(data.assignments.values(), key=lambda a: a.name)
    ]
    return JSONResponse({"courses": courses, "assignments": assignments})


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
    Route("/form-data", form_data, methods=["GET"]),
])
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

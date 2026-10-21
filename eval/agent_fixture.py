"""
Seed a test database for agent evaluation without Canvas access.

Courses: real Cal Poly syllabi from full_syllabi_set/, parsed by the actual
PDF parser, so grading weights, late policies, and confidence scores are
whatever the parser produced (including its mistakes). Courses were chosen to
cover the range of extraction outcomes:

  CSC 349  weights sum to 100%                        (clean)
  CSC 202  10%/day late policy, confidence 3          (penalty found)
  CSC 365  weights sum to 60%                         (partial extraction)
  CSC 430  no weights extracted + one student correction (user override)
  CSC 453  weights sum to 170%                        (clearly wrong)

Assignments: synthetic, with due dates relative to the time of seeding, and
categories assigned by the real name classifier. Data is saved through the
real persistence layer (MongoStore) into a separate database, so the student's
real data is never touched.

Usage:
  python eval/agent_fixture.py          # seeds MONGO_DB_NAME=canvas_assistant_eval
"""

import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import parsers  # noqa: E402
from db import MongoStore  # noqa: E402
from models import Assignment, Course, StudentData, TodoItem  # noqa: E402

EVAL_DB = "canvas_assistant_eval"
STUDENT_ID = "self"
SYLLABI = os.path.join(ROOT, "full_syllabi_set")
CANVAS = "https://canvas.calpoly.edu/courses"

# (course_id, code, name, syllabus file name or its unique prefix)
COURSES = [
    (90001, "CSC-349", "Design and Analysis of Algorithms", "CSC 349 Spring 24.pdf"),
    (90002, "CSC-202", "Data Structures", "CSC 202 Summer 19.pdf"),
    (90003, "CSC-365", "Introduction to Database Systems", "CSC-365 Syllabus Fall'2024"),
    (90004, "CSC-430", "Programming Languages",
     "Syllabus for CSC-430-07-2248 - Programming Languages.pdf"),
    (90005, "CSC-453", "Introduction to Operating Systems", "Syllabus (1).pdf"),
]

# (course_id, name, due in days from now, points, submitted)
ASSIGNMENTS = [
    (90001, "Quiz 4", 1, 20, False),
    (90001, "Homework 5", 2, 100, False),
    (90001, "Homework 4", -3, 100, True),
    (90001, "Homework 6", 9, 100, False),
    (90001, "Final Exam", 20, 300, False),
    (90002, "Lab 5", -2, 10, False),             # past due, not submitted
    (90002, "Lab 6", 1, 10, False),
    (90002, "Project 3: Hash Tables", 5, 100, False),
    (90002, "Midterm 2", 6, 100, False),
    (90003, "Project 1 Milestone", 3, 50, False),
    (90003, "Assignment 3", 4, 40, False),
    (90003, "SQL Sprint", 6, 30, False),          # no category keyword
    (90004, "Assignment 4", 2, 100, False),
    (90004, "Quiz 3", 3, 25, False),
    (90004, "Assignment 5", 12, 100, False),
    (90005, "Program 2: Scheduler", 4, 100, False),  # no category keyword
    (90005, "Midterm", 7, 100, False),
    (90005, "Asgn 1", -5, 50, True),
]

# Student correction applied through the same path as the widget's ✏ form
OVERRIDE = (90004, {"category": "quiz", "weight_pct": 26})


def syllabus_path(name: str) -> str:
    """Path of the syllabus whose file name is `name` or starts with it."""
    exact = os.path.join(SYLLABI, name)
    if os.path.exists(exact):
        return exact
    matches = sorted(f for f in os.listdir(SYLLABI) if f.startswith(name))
    if len(matches) != 1:
        raise FileNotFoundError(f"expected one syllabus matching {name!r} in {SYLLABI}, found {matches}")
    return os.path.join(SYLLABI, matches[0])


def build(now: datetime) -> StudentData:
    data = StudentData(last_fetched=now)
    for cid, code, name, filename in COURSES:
        course = Course(id=cid, name=name, code=code, html_url=f"{CANVAS}/{cid}", fetched_at=now)
        course.syllabus = parsers.parse_syllabus_pdf(syllabus_path(filename), cid)
        data.add_course(course)

    for i, (cid, name, days, points, submitted) in enumerate(ASSIGNMENTS):
        aid = 800000 + i
        due = (now + timedelta(days=days)).replace(hour=6, minute=59, second=0, microsecond=0)  # 11:59 PM PT
        raw = {
            "id": aid,
            "course_id": cid,
            "name": name,
            "due_at": due.isoformat(),
            "points_possible": points,
            "submission_types": ["online_upload"],
            "html_url": f"{CANVAS}/{cid}/assignments/{aid}",
            "submission": {
                "submitted_at": (due - timedelta(days=1)).isoformat() if submitted else None,
                "workflow_state": "submitted" if submitted else "unsubmitted",
            },
        }
        a = Assignment.from_canvas_api(raw)
        data.add_assignment(a)
        if not submitted and days >= 0:
            course = data.courses[cid]
            data.todo_items.append(TodoItem(
                course_id=cid,
                course_name=f"{course.code} - {course.name}",
                assignment=a,
                html_url=a.html_url,
            ))

    parsers.enrich_assignment_categories(data)
    return data


def seed(db_name: str = EVAL_DB) -> MongoStore:
    store = MongoStore(db_name=db_name)
    store.client.drop_database(db_name)
    store = MongoStore(db_name=db_name)  # recreate indexes

    now = datetime.now(timezone.utc)
    store.save_student(STUDENT_ID, canvas_token="", display_name="Eval Student")
    store.save_student_data(STUDENT_ID, build(now))
    cid, override = OVERRIDE
    store.apply_manual_update(STUDENT_ID, "grading_weight", cid, override)
    return store


def describe(store: MongoStore) -> str:
    lines = []
    for cid, code, _, filename in COURSES:
        course = store.get_course(cid)
        cats = ", ".join(
            f"{gc.name} {gc.weight * 100:g}% (conf {gc.confidence}, {gc.source.value})"
            for gc in course.syllabus.grading_categories
        ) or "none"
        lp = course.syllabus.late_policy
        late = f"conf {lp.confidence}, penalty {lp.penalty_per_day}" if lp else "none"
        lines.append(f"{code} [{filename}]\n    weights: {cats}\n    late policy: {late}")
    return "\n".join(lines)


if __name__ == "__main__":
    store = seed(os.getenv("MONGO_DB_NAME", EVAL_DB))
    print(f"seeded {store.db.name}: {store.courses.count_documents({})} courses, "
          f"{store.assignments.count_documents({})} assignments, {store.todo_items.count_documents({})} todos\n")
    print(describe(store))

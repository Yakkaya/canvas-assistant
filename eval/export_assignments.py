"""
Export assignment names from your Canvas courses for hand-labeling.

Pulls every course you are (or were) enrolled in as a student, active and
completed, and writes eval/ground_truth/assignment_categories.csv with a
blank `label` column. The classifier's predictions are intentionally not
included so they don't influence labeling; score.py computes them.

With --offline, no Canvas access is needed: names come from assignments
already stored in the local database (MONGO_DB_NAME, default canvas_assistant)
from earlier refreshes, plus demo/raw_todo.json if present.

Never overwrites an existing CSV (pass --force to regenerate).

Usage:
  python eval/export_assignments.py [--active-only] [--force]
  python eval/export_assignments.py --offline [--force]
"""

import csv
import json
import os
import sys

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import canvas_api  # noqa: E402

OUT = os.path.join(ROOT, "eval", "ground_truth", "assignment_categories.csv")


def student_courses(states: list[str]) -> list[dict]:
    courses: dict[int, dict] = {}
    for state in states:
        url = f"{canvas_api.BASE_URL.rstrip('/')}/api/v1/courses"
        params = {"enrollment_type": "student", "enrollment_state": state, "per_page": 100}
        while url:
            r = requests.get(url, headers=canvas_api.HEADERS, params=params, timeout=20)
            r.raise_for_status()
            for c in r.json():
                if "id" in c and not c.get("access_restricted_by_date"):
                    courses[c["id"]] = c
            url = canvas_api._next_page(r)
            params = {}
    return list(courses.values())


def offline_rows() -> list[list]:
    """Assignment names saved locally from earlier Canvas refreshes."""
    from db import MongoStore

    store = MongoStore()
    codes = {c["_id"]: c.get("code", "") for c in store.courses.find({}, {"code": 1})}
    rows = {}
    for a in store.assignments.find({}, {"course_id": 1, "assignment_id": 1, "name": 1}):
        rows[a["assignment_id"]] = [codes.get(a["course_id"], ""), a["course_id"], a["assignment_id"], a["name"], ""]

    raw_todo = os.path.join(ROOT, "demo", "raw_todo.json")
    if not os.path.exists(raw_todo):
        return sorted(rows.values(), key=lambda r: (r[0], r[3]))
    with open(raw_todo) as f:
        for item in json.load(f):
            a = item.get("assignment") or {}
            if "id" in a and a["id"] not in rows:
                code = (item.get("context_name") or "").split(" - ")[0].rsplit("-", 2)[0]
                rows[a["id"]] = [code, item.get("course_id"), a["id"], a.get("name", ""), ""]
    return sorted(rows.values(), key=lambda r: (r[0], r[3]))


def main() -> None:
    if os.path.exists(OUT) and "--force" not in sys.argv:
        sys.exit(f"{os.path.relpath(OUT, ROOT)} already exists (pass --force to overwrite)")

    if "--offline" in sys.argv:
        rows = offline_rows()
        write(rows)
        return

    states = ["active"] if "--active-only" in sys.argv else ["active", "completed"]
    rows = []
    for course in sorted(student_courses(states), key=lambda c: c.get("course_code", "")):
        code = course.get("course_code") or course.get("name", "")
        try:
            assignments = canvas_api.get_course_assignments(course["id"])
        except requests.HTTPError as e:
            print(f"  skipped {code}: {e}")
            continue
        for a in assignments:
            rows.append([code, course["id"], a["id"], a.get("name", ""), ""])
        print(f"  {code}: {len(assignments)} assignments")

    write(rows)


def write(rows: list[list]) -> None:
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["course", "course_id", "assignment_id", "assignment_name", "label"])
        w.writerows(rows)
    print(f"{len(rows)} assignments from {len({r[1] for r in rows})} courses -> {os.path.relpath(OUT, ROOT)}")


if __name__ == "__main__":
    main()

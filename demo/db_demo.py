"""
Demo: MongoDB Integration Test

Tests the full round-trip:
1. Parse Canvas data (from raw_todo.json)
2. Save to MongoDB
3. Load back from MongoDB
4. Verify data integrity
5. Test verification flow
6. Test upcoming assignments query

Requires: MongoDB running locally (or MONGO_URI in .env)
Run: python demo/db_demo.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import MongoStore
from parsers import load_and_parse_todos

DEMO_STUDENT_ID = "demo_student_001"
DEMO_QUARTER = "2262"
TEST_DB_NAME = "canvas_assistant_test"


def main():
    print("=" * 60)
    print("MongoDB Integration Test")
    print("=" * 60)

    # 1. Parse existing data
    raw_path = Path(__file__).parent / "raw_todo.json"
    if not raw_path.exists():
        print("[!] raw_todo.json not found. Run demo/get_raw_data.py first.")
        return

    with open(raw_path) as f:
        raw_todos = json.load(f)

    data = load_and_parse_todos(raw_todos)
    print(f"\nParsed: {len(data.courses)} courses, {len(data.assignments)} assignments, {len(data.todo_items)} todos")

    # 2. Connect to test database
    store = MongoStore(db_name=TEST_DB_NAME)
    print(f"Connected to MongoDB (db: {TEST_DB_NAME})")

    try:
        # 3. Register student
        store.save_student(DEMO_STUDENT_ID, canvas_token="demo_token_not_real")
        print(f"\nRegistered student: {DEMO_STUDENT_ID}")

        # 4. Save everything
        store.save_student_data(DEMO_STUDENT_ID, data, quarter=DEMO_QUARTER)
        print("Saved all data to MongoDB.")

        # 5. Load back
        loaded = store.load_student_data(DEMO_STUDENT_ID)
        print(f"Loaded back: {len(loaded.courses)} courses, {len(loaded.assignments)} assignments, {len(loaded.todo_items)} todos")

        # 6. Verify round-trip
        print("\n--- Round-trip verification ---")

        assert len(loaded.courses) == len(data.courses), \
            f"Course count: expected {len(data.courses)}, got {len(loaded.courses)}"
        print(f"  Courses: {len(loaded.courses)}/{len(data.courses)} OK")

        assert len(loaded.assignments) == len(data.assignments), \
            f"Assignment count: expected {len(data.assignments)}, got {len(loaded.assignments)}"
        print(f"  Assignments: {len(loaded.assignments)}/{len(data.assignments)} OK")

        for aid, original in data.assignments.items():
            loaded_a = loaded.assignments.get(aid)
            assert loaded_a is not None, f"Missing assignment {aid}"
            assert loaded_a.name == original.name, \
                f"Name mismatch for {aid}: {loaded_a.name} != {original.name}"
            assert loaded_a.course_id == original.course_id
            assert loaded_a.points_possible == original.points_possible
            assert loaded_a.has_submitted == original.has_submitted

        print("  All assignment fields match OK")

        for cid, original in data.courses.items():
            loaded_c = loaded.courses.get(cid)
            assert loaded_c is not None, f"Missing course {cid}"
            assert loaded_c.name == original.name
            assert loaded_c.code == original.code
            assert loaded_c.html_url == original.html_url

        print("  All course fields match OK")

        # 7. Test verification flow
        print("\n--- Verification flow ---")
        first_course_id = list(data.courses.keys())[0]

        assert not store.is_course_verified(first_course_id)
        print(f"  Course {first_course_id} not yet verified: OK")

        store.verify_course(first_course_id, DEMO_STUDENT_ID)
        store.set_verification_status(DEMO_STUDENT_ID, first_course_id, verified=True)

        assert store.is_course_verified(first_course_id)
        print(f"  Course {first_course_id} verified after student confirmation: OK")

        # 8. Test upcoming assignments
        print("\n--- Upcoming assignments ---")
        upcoming = store.get_upcoming_assignments(DEMO_STUDENT_ID, days=30)
        print(f"  Due in next 30 days: {len(upcoming)}")
        for a in upcoming:
            due_str = a.due_at.strftime("%b %d %H:%M") if a.due_at else "No due date"
            print(f"    [{a.course_id}] {a.name} - Due: {due_str}")

        # 9. Test to_dict still works on loaded data
        print("\n--- LLM context from DB data ---")
        context = loaded.to_dict()
        print(f"  to_dict() produced {len(context['courses'])} courses, {len(context['assignments'])} assignments")
        print(json.dumps(context, indent=2, default=str))

        print("\n" + "=" * 60)
        print("ALL TESTS PASSED")
        print("=" * 60)

    finally:
    #     store.client.drop_database(TEST_DB_NAME)
        store.close()
        # print(f"\nCleaned up test database: {TEST_DB_NAME}")


if __name__ == "__main__":
    main()

"""
MongoDB persistence layer for Canvas student assistant.

Collections:
  - courses: shared course + syllabus data (same for all students)
  - students: per-user profiles + tokens (private)
  - assignments: per-student assignment data (private)
  - todo_items: current todo items per student (private)
"""

import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection

from models import (
    Assignment,
    AssignmentCategory,
    Course,
    DataSource,
    GradingCategory,
    LatePolicy,
    StudentData,
    SubmissionType,
    SyllabusInfo,
    TodoItem,
)

load_dotenv()


class MongoStore:
    """MongoDB storage for Canvas student assistant."""

    def __init__(self, uri: str = None, db_name: str = "canvas_assistant"):
        self.client = MongoClient(uri or os.getenv("MONGO_URI", "mongodb://localhost:27017"))
        self.db = self.client[db_name]

        # Collections
        self.courses: Collection = self.db["courses"]
        self.students: Collection = self.db["students"]
        self.assignments: Collection = self.db["assignments"]
        self.todo_items: Collection = self.db["todo_items"]

        self._ensure_indexes()

    def _ensure_indexes(self):
        """Create indexes on first run (idempotent)."""
        self.courses.create_index([("code", ASCENDING), ("quarter", ASCENDING)])
        self.assignments.create_index([("student_id", ASCENDING), ("course_id", ASCENDING)])
        self.assignments.create_index(
            [("student_id", ASCENDING), ("assignment_id", ASCENDING)], unique=True
        )
        self.assignments.create_index([("student_id", ASCENDING), ("due_at", ASCENDING)])
        self.todo_items.create_index([("student_id", ASCENDING)])
        self.todo_items.create_index(
            [("student_id", ASCENDING), ("assignment_id", ASCENDING)], unique=True
        )

    def close(self):
        self.client.close()

    # =========================================================================
    # Manual data corrections (student overrides)
    # =========================================================================

    def apply_manual_update(
        self, student_id: str, update_type: str, course_id: int | None, data: dict
    ) -> None:
        """Apply a student-supplied data correction, stamped as USER_OVERRIDE."""
        if update_type == "grading_weight":
            category_str = data.get("category", "other").lower()
            try:
                assignment_cat = AssignmentCategory(category_str)
            except ValueError:
                assignment_cat = AssignmentCategory.OTHER

            gc_doc = {
                "name": category_str.title(),
                "weight": float(data.get("weight_pct", 0)) / 100.0,
                "assignment_category": assignment_cat.value,
                "drop_lowest": 0,
                "source": DataSource.USER_OVERRIDE.value,
                "confidence": 5,
            }

            course_doc = self.courses.find_one({"_id": course_id})
            if not course_doc:
                raise ValueError(f"Course {course_id} not found")

            syllabus = course_doc.get("syllabus") or {
                "grading_categories": [],
                "grading_scale": {},
                "source": DataSource.USER_OVERRIDE.value,
            }
            cats = syllabus.get("grading_categories", [])
            for i, cat in enumerate(cats):
                if cat.get("assignment_category") == assignment_cat.value:
                    cats[i] = gc_doc
                    break
            else:
                cats.append(gc_doc)
            syllabus["grading_categories"] = cats

            self.courses.update_one(
                {"_id": course_id},
                {"$set": {"syllabus": syllabus, "updated_at": datetime.now(timezone.utc)}},
            )

        elif update_type == "late_policy":
            lp_doc = {
                "allows_late": bool(data.get("allows_late", True)),
                "penalty_per_day": (
                    float(data["penalty_per_day"]) / 100.0
                    if data.get("penalty_per_day") is not None
                    else None
                ),
                "penalty_type": "percentage",
                "max_days_late": data.get("max_days_late"),
                "grace_period_hours": 0,
                "raw_text": None,
                "source": DataSource.USER_OVERRIDE.value,
                "confidence": 5,
            }

            course_doc = self.courses.find_one({"_id": course_id})
            if not course_doc:
                raise ValueError(f"Course {course_id} not found")

            syllabus = course_doc.get("syllabus") or {
                "grading_categories": [],
                "grading_scale": {},
                "source": DataSource.USER_OVERRIDE.value,
            }
            syllabus["late_policy"] = lp_doc

            self.courses.update_one(
                {"_id": course_id},
                {"$set": {"syllabus": syllabus, "updated_at": datetime.now(timezone.utc)}},
            )

        elif update_type == "assignment_category":
            assignment_id = int(data.get("assignment_id", 0))
            category_str = data.get("category", "").lower()
            try:
                category = AssignmentCategory(category_str)
            except ValueError:
                raise ValueError(f"Invalid category: {category_str}")

            result = self.assignments.update_one(
                {"student_id": student_id, "assignment_id": assignment_id},
                {"$set": {
                    "category": category.value,
                    "confidence": 5,
                    "source": DataSource.USER_OVERRIDE.value,
                }},
            )
            if result.matched_count == 0:
                raise ValueError(f"Assignment {assignment_id} not found")

        else:
            raise ValueError(f"Unknown update_type: {update_type}")

    # =========================================================================
    # Course operations (shared data)
    # =========================================================================

    def save_course(self, course: Course, quarter: str = "") -> None:
        """Upsert course data. Syllabus included if present."""
        doc = {
            "_id": course.id,
            "name": course.name,
            "code": course.code,
            "html_url": course.html_url,
            "quarter": quarter,
            "updated_at": datetime.now(timezone.utc),
        }
        if course.syllabus:
            doc["syllabus"] = self._syllabus_to_doc(course.syllabus)

        self.courses.update_one({"_id": course.id}, {"$set": doc}, upsert=True)

    def get_course(self, course_id: int) -> Optional[Course]:
        """Load course from DB, including syllabus if present."""
        doc = self.courses.find_one({"_id": course_id})
        if not doc:
            return None
        return self._doc_to_course(doc)

    def verify_course(self, course_id: int, student_id: str) -> None:
        """Mark course syllabus data as verified by a student."""
        self.courses.update_one(
            {"_id": course_id},
            {
                "$addToSet": {"verified_by": student_id},
                "$set": {"verified_at": datetime.now(timezone.utc)},
            },
        )

    def is_course_verified(self, course_id: int) -> bool:
        """Check if any student has verified this course's data."""
        doc = self.courses.find_one({"_id": course_id}, {"verified_by": 1})
        return bool(doc and doc.get("verified_by"))

    # =========================================================================
    # Student operations (private data)
    # =========================================================================

    def save_student(self, student_id: str, canvas_token: str, display_name: str = "") -> None:
        """Create or update student profile."""
        self.students.update_one(
            {"_id": student_id},
            {
                "$set": {
                    "canvas_token": canvas_token,  # TODO: encrypt in production
                    "display_name": display_name,
                    "last_active": datetime.now(timezone.utc),
                },
                "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
            },
            upsert=True,
        )

    def enroll_student(self, student_id: str, course_id: int) -> None:
        """Add course to student's enrollment list."""
        self.students.update_one(
            {"_id": student_id},
            {"$addToSet": {"enrolled_courses": course_id}},
        )

    def set_verification_status(
        self, student_id: str, course_id: int, verified: bool
    ) -> None:
        """Track that a student has verified a course's parsed data."""
        self.students.update_one(
            {"_id": student_id},
            {
                "$set": {
                    f"course_verifications.{course_id}.verified": verified,
                    f"course_verifications.{course_id}.verified_at": datetime.now(
                        timezone.utc
                    ),
                }
            },
        )

    # =========================================================================
    # Assignment operations (per-student)
    # =========================================================================

    def save_assignments(self, student_id: str, assignments: list[Assignment]) -> None:
        """Bulk upsert assignments for a student."""
        for a in assignments:
            doc = self._assignment_to_doc(a, student_id)
            self.assignments.update_one(
                {"student_id": student_id, "assignment_id": a.id},
                {"$set": doc},
                upsert=True,
            )

    def get_assignments_for_course(
        self, student_id: str, course_id: int
    ) -> list[Assignment]:
        """Get all assignments for a student in a course."""
        docs = self.assignments.find({"student_id": student_id, "course_id": course_id})
        return [self._doc_to_assignment(d) for d in docs]

    def get_upcoming_assignments(
        self, student_id: str, days: int = 7
    ) -> list[Assignment]:
        """Get assignments due within N days."""
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days)
        docs = self.assignments.find(
            {
                "student_id": student_id,
                "due_at": {"$gte": now, "$lte": cutoff},
            }
        ).sort("due_at", ASCENDING)
        return [self._doc_to_assignment(d) for d in docs]

    # =========================================================================
    # Todo operations
    # =========================================================================

    def refresh_todos(self, student_id: str, todos: list[TodoItem]) -> None:
        """Replace all todos for a student (called after Canvas API fetch)."""
        self.todo_items.delete_many({"student_id": student_id})
        if todos:
            docs = [self._todo_to_doc(t, student_id) for t in todos]
            self.todo_items.insert_many(docs)

    def get_todos(self, student_id: str) -> list[dict]:
        """Get current todos for a student."""
        return list(self.todo_items.find({"student_id": student_id}))

    # =========================================================================
    # Full StudentData round-trip
    # =========================================================================

    def save_student_data(
        self, student_id: str, data: StudentData, quarter: str = ""
    ) -> None:
        """Persist a full StudentData object (courses, assignments, todos)."""
        for course in data.courses.values():
            self.save_course(course, quarter)
            self.enroll_student(student_id, course.id)

        self.save_assignments(student_id, list(data.assignments.values()))
        self.refresh_todos(student_id, data.todo_items)

    def load_student_data(self, student_id: str) -> StudentData:
        """Reconstruct a StudentData object from the database."""
        student_doc = self.students.find_one({"_id": student_id})
        if not student_doc:
            return StudentData()

        data = StudentData()

        # Load enrolled courses
        for cid in student_doc.get("enrolled_courses", []):
            course = self.get_course(cid)
            if course:
                data.add_course(course)

        # Load all assignments for enrolled courses
        for cid in data.courses:
            for a in self.get_assignments_for_course(student_id, cid):
                data.add_assignment(a)

        # Load todos, linking back to assignment objects
        for td in self.get_todos(student_id):
            aid = td.get("assignment_id")
            if aid in data.assignments:
                data.todo_items.append(
                    TodoItem(
                        course_id=td["course_id"],
                        course_name=td.get("course_name", ""),
                        assignment=data.assignments[aid],
                        html_url=td.get("html_url", ""),
                        todo_type=td.get("todo_type", "submitting"),
                    )
                )

        return data

    # =========================================================================
    # Private serialization helpers
    # =========================================================================

    def _syllabus_to_doc(self, s: SyllabusInfo) -> dict:
        doc = {
            "grading_categories": [
                {
                    "name": gc.name,
                    "weight": gc.weight,
                    "assignment_category": gc.assignment_category.value,
                    "drop_lowest": gc.drop_lowest,
                    "source": gc.source.value,
                    "confidence": gc.confidence,
                }
                for gc in s.grading_categories
            ],
            "grading_scale": s.grading_scale,
            "late_policy": self._late_policy_to_doc(s.late_policy) if s.late_policy else None,
            "source": s.source.value,
            "parsed_at": s.parsed_at,
            "assumed_timezone": s.assumed_timezone,
        }
        return doc

    def _late_policy_to_doc(self, lp: LatePolicy) -> dict:
        return {
            "allows_late": lp.allows_late,
            "penalty_per_day": lp.penalty_per_day,
            "penalty_type": lp.penalty_type,
            "max_days_late": lp.max_days_late,
            "grace_period_hours": lp.grace_period_hours,
            "raw_text": lp.raw_text,
            "source": lp.source.value,
            "confidence": lp.confidence,
        }

    def _assignment_to_doc(self, a: Assignment, student_id: str) -> dict:
        return {
            "student_id": student_id,
            "assignment_id": a.id,
            "course_id": a.course_id,
            "name": a.name,
            "due_at": a.due_at,
            "unlock_at": a.unlock_at,
            "lock_at": a.lock_at,
            "points_possible": a.points_possible,
            "grading_type": a.grading_type,
            "omit_from_final_grade": a.omit_from_final_grade,
            "submission_types": [st.value for st in a.submission_types],
            "has_submitted": a.has_submitted,
            "is_locked": a.is_locked,
            "html_url": a.html_url,
            "is_quiz": a.is_quiz,
            "quiz_id": a.quiz_id,
            "category": a.category.value if a.category else None,
            "weight_percentage": a.weight_percentage,
            "confidence": a.confidence,
            "source": a.source.value,
            "fetched_at": datetime.now(timezone.utc),
        }

    def _todo_to_doc(self, t: TodoItem, student_id: str) -> dict:
        return {
            "student_id": student_id,
            "course_id": t.course_id,
            "course_name": t.course_name,
            "assignment_id": t.assignment.id,
            "todo_type": t.todo_type,
            "html_url": t.html_url,
            "fetched_at": datetime.now(timezone.utc),
        }

    def _doc_to_course(self, doc: dict) -> Course:
        course = Course(
            id=doc["_id"],
            name=doc.get("name", ""),
            code=doc.get("code", ""),
            html_url=doc.get("html_url"),
        )
        if doc.get("syllabus"):
            course.syllabus = self._doc_to_syllabus(doc["syllabus"], doc["_id"])
        return course

    def _doc_to_syllabus(self, doc: dict, course_id: int) -> SyllabusInfo:
        s = SyllabusInfo(course_id=course_id)
        s.grading_categories = [
            GradingCategory(
                name=gc["name"],
                weight=gc["weight"],
                assignment_category=AssignmentCategory(gc["assignment_category"]),
                drop_lowest=gc.get("drop_lowest", 0),
                source=DataSource(gc.get("source", "syllabus_html")),
                confidence=gc.get("confidence", 4),
            )
            for gc in doc.get("grading_categories", [])
        ]
        s.grading_scale = doc.get("grading_scale", {})
        if doc.get("late_policy"):
            lp = doc["late_policy"]
            s.late_policy = LatePolicy(
                allows_late=lp.get("allows_late", True),
                penalty_per_day=lp.get("penalty_per_day"),
                penalty_type=lp.get("penalty_type", "percentage"),
                max_days_late=lp.get("max_days_late"),
                grace_period_hours=lp.get("grace_period_hours", 0),
                raw_text=lp.get("raw_text"),
                source=DataSource(lp.get("source", "syllabus_html")),
                confidence=lp.get("confidence", 3),
            )
        s.source = DataSource(doc.get("source", "syllabus_html"))
        s.parsed_at = doc.get("parsed_at")
        return s

    def _doc_to_assignment(self, doc: dict) -> Assignment:
        sub_types = []
        for st in doc.get("submission_types", []):
            try:
                sub_types.append(SubmissionType(st))
            except ValueError:
                pass

        return Assignment(
            id=doc["assignment_id"],
            course_id=doc["course_id"],
            name=doc.get("name", ""),
            due_at=doc.get("due_at"),
            unlock_at=doc.get("unlock_at"),
            lock_at=doc.get("lock_at"),
            points_possible=doc.get("points_possible"),
            grading_type=doc.get("grading_type", "points"),
            omit_from_final_grade=doc.get("omit_from_final_grade", False),
            submission_types=sub_types,
            has_submitted=doc.get("has_submitted", False),
            is_locked=doc.get("is_locked", False),
            html_url=doc.get("html_url"),
            is_quiz=doc.get("is_quiz", False),
            quiz_id=doc.get("quiz_id"),
            category=AssignmentCategory(doc["category"]) if doc.get("category") else None,
            weight_percentage=doc.get("weight_percentage"),
            confidence=doc.get("confidence", 5),
            source=DataSource(doc.get("source", "canvas_api")),
        )

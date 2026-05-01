import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone

from models import (
    Assignment,
    AssignmentCategory,
    Course,
    DataSource,
    GradingCategory,
    LatePolicy,
    AttendancePolicy,
    SyllabusDate,
    SyllabusInfo,
    StudentData,
    SubmissionType,
    TodoItem,
)


# ---------------------------------------------------------------------------
# Default confidence and source values
# ---------------------------------------------------------------------------

def test_grading_category_defaults():
    gc = GradingCategory(name="Homework", weight=0.30)
    assert gc.confidence == 5
    assert gc.source == DataSource.SYLLABUS_HTML
    assert gc.assignment_category == AssignmentCategory.OTHER


def test_late_policy_defaults():
    lp = LatePolicy()
    assert lp.confidence == 5
    assert lp.source == DataSource.SYLLABUS_HTML
    assert lp.allows_late is True


def test_attendance_policy_defaults():
    ap = AttendancePolicy()
    assert ap.confidence == 5
    assert ap.source == DataSource.SYLLABUS_HTML


def test_syllabus_date_defaults():
    sd = SyllabusDate(date=datetime.now(timezone.utc), description="Midterm")
    assert sd.confidence == 5
    assert sd.source == DataSource.SYLLABUS_HTML


def test_assignment_defaults():
    a = Assignment(id=1, course_id=2, name="HW 1")
    assert a.confidence == 5
    assert a.source == DataSource.CANVAS_API


# ---------------------------------------------------------------------------
# Assignment.from_canvas_api
# ---------------------------------------------------------------------------

def test_assignment_from_canvas_api_basic():
    raw = {
        "id": 123,
        "course_id": 456,
        "name": "Midterm Exam",
        "due_at": "2025-05-15T23:59:00Z",
        "points_possible": 100.0,
        "grading_type": "points",
        "omit_from_final_grade": False,
        "submission_types": ["online_upload"],
        "has_submitted_submissions": False,
        "locked_for_user": False,
        "html_url": "https://canvas.calpoly.edu/courses/456/assignments/123",
    }
    a = Assignment.from_canvas_api(raw)
    assert a.id == 123
    assert a.course_id == 456
    assert a.name == "Midterm Exam"
    assert a.points_possible == 100.0
    assert a.due_at is not None
    assert a.has_submitted is False
    assert SubmissionType.ONLINE_UPLOAD in a.submission_types


def test_assignment_from_canvas_api_missing_due_date():
    raw = {"id": 1, "course_id": 2, "name": "Survey", "submission_types": []}
    a = Assignment.from_canvas_api(raw)
    assert a.due_at is None


def test_assignment_from_canvas_api_unknown_submission_type():
    raw = {"id": 1, "course_id": 2, "name": "X", "submission_types": ["not_a_real_type"]}
    a = Assignment.from_canvas_api(raw)
    assert a.submission_types == []


# ---------------------------------------------------------------------------
# Course.from_context_name
# ---------------------------------------------------------------------------

def test_course_from_context_name():
    course = Course.from_context_name(1, "CSC-313-01-2262 - Teaching Computing")
    assert course.code == "CSC-313"
    assert course.name == "Teaching Computing"
    assert course.id == 1


def test_course_from_context_name_no_dash():
    course = Course.from_context_name(5, "Intro to Art")
    assert course.id == 5


# ---------------------------------------------------------------------------
# StudentData.to_dict
# ---------------------------------------------------------------------------

def test_student_data_to_dict_structure():
    data = StudentData()
    course = Course(id=1, name="Test Course", code="CSC-101", html_url="http://example.com")
    data.add_course(course)

    a = Assignment(
        id=10,
        course_id=1,
        name="HW 1",
        due_at=datetime.now(timezone.utc),
        points_possible=50.0,
        category=AssignmentCategory.HOMEWORK,
    )
    data.add_assignment(a)

    d = data.to_dict()
    assert "courses" in d
    assert "assignments" in d
    assert "todo_items" in d
    assert len(d["courses"]) == 1
    assert len(d["assignments"]) == 1
    assert isinstance(d["assignments"][0]["due_at"], str)


def test_student_data_to_dict_empty():
    data = StudentData()
    d = data.to_dict()
    assert d["courses"] == []
    assert d["assignments"] == []
    assert d["todo_items"] == []


def test_student_data_to_dict_no_due_date():
    data = StudentData()
    course = Course(id=1, name="C", code="C-1")
    data.add_course(course)
    a = Assignment(id=1, course_id=1, name="No due date")
    data.add_assignment(a)
    d = data.to_dict()
    assert d["assignments"][0]["due_at"] is None


def test_student_data_add_and_get():
    data = StudentData()
    c = Course(id=7, name="Math", code="MATH-101")
    data.add_course(c)
    assert 7 in data.courses

    a = Assignment(id=99, course_id=7, name="Quiz 1")
    data.add_assignment(a)
    assert 99 in data.assignments
    assert data.get_assignments_for_course(7) == [a]


def test_student_data_get_upcoming_assignments():
    from datetime import timedelta

    data = StudentData()
    now = datetime.now(timezone.utc)

    a_soon = Assignment(id=1, course_id=1, name="Soon", due_at=now + timedelta(days=2))
    a_far = Assignment(id=2, course_id=1, name="Far", due_at=now + timedelta(days=30))
    a_past = Assignment(id=3, course_id=1, name="Past", due_at=now - timedelta(days=1))

    data.add_assignment(a_soon)
    data.add_assignment(a_far)
    data.add_assignment(a_past)

    upcoming = data.get_upcoming_assignments(days=7)
    ids = {a.id for a in upcoming}
    assert 1 in ids
    assert 2 not in ids
    assert 3 not in ids


# ---------------------------------------------------------------------------
# SyllabusInfo.get_weight_for_category
# ---------------------------------------------------------------------------

def test_syllabus_get_weight_for_category():
    s = SyllabusInfo(course_id=1)
    s.grading_categories = [
        GradingCategory(name="Homework", weight=0.30, assignment_category=AssignmentCategory.HOMEWORK),
        GradingCategory(name="Exams", weight=0.70, assignment_category=AssignmentCategory.EXAM),
    ]
    assert s.get_weight_for_category(AssignmentCategory.HOMEWORK) == 0.30
    assert s.get_weight_for_category(AssignmentCategory.EXAM) == 0.70
    assert s.get_weight_for_category(AssignmentCategory.QUIZ) is None

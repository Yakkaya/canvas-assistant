import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone

from db import MongoStore
from models import Assignment
from parsers import _extract_late_policy
from prompts import stamp_message


# ---------------------------------------------------------------------------
# has_submitted comes from the student's own submission object
# ---------------------------------------------------------------------------

def _raw(submission=None, **extra):
    raw = {"id": 1, "course_id": 2, "name": "HW 1", **extra}
    if submission is not None:
        raw["submission"] = submission
    return raw


def test_has_submitted_ignores_other_students_submissions():
    a = Assignment.from_canvas_api(_raw(has_submitted_submissions=True))
    assert a.has_submitted is False


def test_has_submitted_from_submitted_at():
    a = Assignment.from_canvas_api(_raw({"submitted_at": "2026-04-01T10:00:00Z", "workflow_state": "submitted"}))
    assert a.has_submitted is True


def test_has_submitted_unsubmitted():
    a = Assignment.from_canvas_api(_raw({"submitted_at": None, "workflow_state": "unsubmitted"}))
    assert a.has_submitted is False


def test_has_submitted_graded_but_missing():
    a = Assignment.from_canvas_api(_raw({"submitted_at": None, "workflow_state": "graded", "missing": True}))
    assert a.has_submitted is False


# ---------------------------------------------------------------------------
# Late penalty scaling
# ---------------------------------------------------------------------------

def test_late_penalty_whole_percent():
    assert abs(_extract_late_policy("Late work loses 10% per day.").penalty_per_day - 0.10) < 1e-9


def test_late_penalty_fractional_percent():
    assert abs(_extract_late_policy("Late work loses 0.5% per day.").penalty_per_day - 0.005) < 1e-9


def test_late_penalty_bare_number():
    assert abs(_extract_late_policy("Late work loses 20 per day.").penalty_per_day - 0.20) < 1e-9


# ---------------------------------------------------------------------------
# Student corrections survive a syllabus re-parse
# ---------------------------------------------------------------------------

def _gc(cat, weight, source):
    return {"name": cat.title(), "weight": weight, "assignment_category": cat, "source": source}


def test_keep_overrides_replaces_parsed_category():
    new = {"grading_categories": [_gc("homework", 0.3, "syllabus_html"), _gc("exam", 0.5, "syllabus_html")],
           "late_policy": {"allows_late": True, "source": "syllabus_html"}}
    old = {"grading_categories": [_gc("homework", 0.4, "user_override"), _gc("exam", 0.5, "syllabus_html")],
           "late_policy": {"allows_late": False, "source": "user_override"}}
    merged = MongoStore._keep_overrides(new, old)
    by_cat = {gc["assignment_category"]: gc for gc in merged["grading_categories"]}
    assert by_cat["homework"]["weight"] == 0.4
    assert by_cat["homework"]["source"] == "user_override"
    assert by_cat["exam"]["source"] == "syllabus_html"
    assert len(merged["grading_categories"]) == 2
    assert merged["late_policy"]["allows_late"] is False


def test_keep_overrides_without_previous_syllabus():
    new = {"grading_categories": [_gc("lab", 0.2, "syllabus_html")], "late_policy": None}
    assert MongoStore._keep_overrides(new, None) == new


# ---------------------------------------------------------------------------
# Current time stamp
# ---------------------------------------------------------------------------

def test_stamp_message_uses_pacific_daylight_time():
    msg = stamp_message("hi", now=datetime(2026, 5, 1, 19, 0, tzinfo=timezone.utc))
    assert msg.startswith("[Current time: Friday, May 01, 2026 12:00 PM PDT]")
    assert msg.endswith("\nhi")


def test_stamp_message_uses_pacific_standard_time():
    msg = stamp_message("hi", now=datetime(2026, 1, 15, 20, 0, tzinfo=timezone.utc))
    assert "12:00 PM PST" in msg


# ---------------------------------------------------------------------------
# "late" must be a whole word
# ---------------------------------------------------------------------------

def test_late_policy_ignores_late_inside_other_words():
    assert _extract_late_policy("Obligations may conflate. Use the template. Translate it.") is None


def test_late_policy_raw_text_centers_on_penalty():
    filler = "Lorem ipsum. " * 30
    policy = _extract_late_policy(f"Turn work in late? {filler} Late work loses 10% per day.")
    assert "10% per day" in policy.raw_text


# ---------------------------------------------------------------------------
# PDF-derived values are labeled as coming from the PDF
# ---------------------------------------------------------------------------

def test_pdf_text_values_have_pdf_source():
    import fitz
    from models import DataSource
    from parsers import parse_syllabus_pdf
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "Grading\nHomework: 30%\nExams: 70%\nLate work loses 10% per day.")
    s = parse_syllabus_pdf(doc.tobytes(), course_id=1)
    assert s.grading_categories
    assert all(gc.source == DataSource.SYLLABUS_PDF for gc in s.grading_categories)
    assert s.late_policy.source == DataSource.SYLLABUS_PDF

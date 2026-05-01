import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pathlib import Path

import pytest

from models import AssignmentCategory, DataSource
from parsers import (
    _extract_grading_weights,
    _extract_late_policy,
    enrich_assignment_categories,
    infer_assignment_category,
    load_and_parse_todos,
    parse_syllabus_html,
)

RAW_TODO_PATH = Path(__file__).parent.parent / "demo" / "raw_todo.json"


# ---------------------------------------------------------------------------
# infer_assignment_category
# ---------------------------------------------------------------------------

def test_infer_category_homework():
    cat, conf = infer_assignment_category("HW 1")
    assert cat == AssignmentCategory.HOMEWORK
    assert conf == 4


def test_infer_category_homework_full_word():
    cat, conf = infer_assignment_category("Homework 3")
    assert cat == AssignmentCategory.HOMEWORK
    assert conf == 4


def test_infer_category_exam():
    cat, conf = infer_assignment_category("Final Exam")
    assert cat == AssignmentCategory.EXAM
    assert conf == 4


def test_infer_category_midterm():
    cat, conf = infer_assignment_category("Midterm 2")
    assert cat == AssignmentCategory.EXAM
    assert conf == 4


def test_infer_category_quiz():
    cat, conf = infer_assignment_category("Reading Quiz 3")
    assert cat == AssignmentCategory.QUIZ
    assert conf == 4


def test_infer_category_lab():
    cat, conf = infer_assignment_category("Lab Exercise 5")
    assert cat == AssignmentCategory.LAB
    assert conf == 4


def test_infer_category_project():
    cat, conf = infer_assignment_category("Group Project Milestone")
    assert cat == AssignmentCategory.PROJECT
    assert conf == 4


def test_infer_category_other():
    cat, conf = infer_assignment_category("totally random title 42")
    assert cat == AssignmentCategory.OTHER
    assert conf == 1


def test_infer_category_case_insensitive():
    cat1, _ = infer_assignment_category("HOMEWORK 1")
    cat2, _ = infer_assignment_category("homework 1")
    assert cat1 == cat2 == AssignmentCategory.HOMEWORK


# ---------------------------------------------------------------------------
# _extract_grading_weights
# ---------------------------------------------------------------------------

def test_extract_weights_standard_colon():
    text = "Homework: 30%\nExams: 40%\nQuizzes: 30%"
    cats = _extract_grading_weights(text)
    by_cat = {gc.assignment_category: gc for gc in cats}
    assert AssignmentCategory.HOMEWORK in by_cat
    assert abs(by_cat[AssignmentCategory.HOMEWORK].weight - 0.30) < 0.01
    assert AssignmentCategory.EXAM in by_cat
    assert abs(by_cat[AssignmentCategory.EXAM].weight - 0.40) < 0.01


def test_extract_weights_reverse_order():
    text = "30% – Labs\n40% - Exams\n30% Homework"
    cats = _extract_grading_weights(text)
    assert len(cats) >= 2


def test_extract_weights_confidence_is_4():
    text = "Homework: 30%\nExams: 70%"
    cats = _extract_grading_weights(text)
    assert all(gc.confidence == 4 for gc in cats)


def test_extract_weights_returns_empty_for_no_match():
    text = "There are no grading policies listed here."
    cats = _extract_grading_weights(text)
    assert cats == []


def test_extract_weights_accumulates_numbered_exams():
    # Parser's numbered-exam pattern requires weight on the next line
    text = "Exam 1:\n20%\nExam 2:\n20%"
    cats = _extract_grading_weights(text)
    by_cat = {gc.assignment_category: gc for gc in cats}
    assert AssignmentCategory.EXAM in by_cat
    assert abs(by_cat[AssignmentCategory.EXAM].weight - 0.40) < 0.01


# ---------------------------------------------------------------------------
# _extract_late_policy
# ---------------------------------------------------------------------------

def test_late_policy_no_late():
    policy = _extract_late_policy("No late work accepted. All deadlines are firm.")
    assert policy is not None
    assert policy.allows_late is False
    assert policy.confidence == 4


def test_late_policy_penalty_per_day():
    policy = _extract_late_policy("Assignments lose 10% per day when submitted late.")
    assert policy is not None
    assert policy.allows_late is True
    assert policy.confidence == 3


def test_late_policy_unclear_mention():
    policy = _extract_late_policy("Late submissions may be penalized at instructor discretion.")
    assert policy is not None
    assert policy.confidence == 2


def test_late_policy_no_mention_returns_none():
    policy = _extract_late_policy("Please submit all work on time.")
    assert policy is None


def test_late_policy_raw_text_captured():
    text = "No late work accepted. Contact instructor for extensions."
    policy = _extract_late_policy(text)
    assert policy is not None
    assert policy.raw_text  # should not be empty


# ---------------------------------------------------------------------------
# parse_syllabus_html
# ---------------------------------------------------------------------------

def test_parse_syllabus_html_grading_weights():
    html = "<p>Grading breakdown: Homework: 40%, Exams: 60%</p>"
    syllabus = parse_syllabus_html(html, course_id=99)
    assert syllabus.course_id == 99
    assert syllabus.grading_categories
    by_cat = {gc.assignment_category for gc in syllabus.grading_categories}
    assert AssignmentCategory.HOMEWORK in by_cat


def test_parse_syllabus_html_late_policy():
    html = "<p>Grading: Homework: 50%, Projects: 50%</p><p>No late work accepted.</p>"
    syllabus = parse_syllabus_html(html, course_id=1)
    assert syllabus.late_policy is not None
    assert syllabus.late_policy.allows_late is False


def test_parse_syllabus_html_source():
    html = "<p>Homework: 100%</p>"
    syllabus = parse_syllabus_html(html, course_id=2)
    assert syllabus.source == DataSource.SYLLABUS_HTML


def test_parse_syllabus_html_no_grading():
    html = "<p>Welcome to the course! See Canvas for details.</p>"
    syllabus = parse_syllabus_html(html, course_id=3)
    assert syllabus.grading_categories == []
    assert syllabus.late_policy is None


# ---------------------------------------------------------------------------
# load_and_parse_todos (integration — uses raw_todo.json fixture)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not RAW_TODO_PATH.exists(), reason="raw_todo.json not present")
def test_load_and_parse_todos_basic():
    with open(RAW_TODO_PATH) as f:
        raw = json.load(f)
    data = load_and_parse_todos(raw)
    assert data.courses
    assert data.assignments
    assert data.todo_items


@pytest.mark.skipif(not RAW_TODO_PATH.exists(), reason="raw_todo.json not present")
def test_load_and_parse_todos_categories_inferred():
    with open(RAW_TODO_PATH) as f:
        raw = json.load(f)
    data = load_and_parse_todos(raw)
    for a in data.assignments.values():
        assert a.category is not None
        assert 1 <= a.confidence <= 5


@pytest.mark.skipif(not RAW_TODO_PATH.exists(), reason="raw_todo.json not present")
def test_load_and_parse_todos_courses_match_assignments():
    with open(RAW_TODO_PATH) as f:
        raw = json.load(f)
    data = load_and_parse_todos(raw)
    course_ids = set(data.courses.keys())
    for a in data.assignments.values():
        assert a.course_id in course_ids

"""
Data models for Canvas student data aggregation.

These models support:
- Canvas API data (courses, assignments, todos)
- Syllabus parsing (HTML and PDF)
- Priority calculation for LLM agent
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class SubmissionType(Enum):
    ONLINE_TEXT = "online_text_entry"
    ONLINE_URL = "online_url"
    ONLINE_UPLOAD = "online_upload"
    EXTERNAL_TOOL = "external_tool"
    ONLINE_QUIZ = "online_quiz"
    DISCUSSION = "discussion_topic"
    MEDIA_RECORDING = "media_recording"


class AssignmentCategory(Enum):
    """Common syllabus grading categories."""
    HOMEWORK = "homework"
    PROJECT = "project"
    EXAM = "exam"
    QUIZ = "quiz"
    LAB = "lab"
    PARTICIPATION = "participation"
    READING = "reading"
    DISCUSSION = "discussion"
    FINAL = "final"
    MIDTERM = "midterm"
    OTHER = "other"


class DataSource(Enum):
    """Where the data came from - useful for conflict resolution."""
    CANVAS_API = "canvas_api"
    SYLLABUS_HTML = "syllabus_html"
    SYLLABUS_PDF = "syllabus_pdf"
    USER_OVERRIDE = "user_override"


# =============================================================================
# Core Canvas Data Models
# =============================================================================

@dataclass
class Course:
    """A Canvas course."""
    id: int
    name: str  # e.g., "CSC-364-05-2262 - Introduction to Networked..."
    code: str  # e.g., "CSC-364"

    # Canvas URL (e.g., "https://canvas.calpoly.edu/courses/172956")
    html_url: Optional[str] = None

    # Syllabus data (populated after parsing)
    syllabus: Optional["SyllabusInfo"] = None

    # Timestamps
    fetched_at: Optional[datetime] = None

    @classmethod
    def from_context_name(cls, course_id: int, context_name: str) -> "Course":
        """Parse course from Canvas context_name field.

        Example: "CSC-313-01-2262 - Teaching Computing"
        """
        parts = context_name.split(" - ", 1)
        code = parts[0].rsplit("-", 1)[0] if parts else context_name  # "CSC-313-01" -> "CSC-313"
        name = parts[1] if len(parts) > 1 else context_name

        # Extract just the course code (e.g., "CSC-313")
        code_parts = code.split("-")
        if len(code_parts) >= 2:
            code = f"{code_parts[0]}-{code_parts[1]}"

        return cls(id=course_id, name=name, code=code)


@dataclass
class Assignment:
    """An assignment from Canvas."""
    id: int
    course_id: int
    name: str

    # Dates (all in UTC from Canvas)
    due_at: Optional[datetime] = None
    unlock_at: Optional[datetime] = None
    lock_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Grading
    points_possible: Optional[float] = None
    grading_type: str = "points"  # points, letter_grade, pass_fail, etc.
    omit_from_final_grade: bool = False

    # Submission info
    submission_types: list[SubmissionType] = field(default_factory=list)
    has_submitted: bool = False
    is_locked: bool = False
    lock_explanation: Optional[str] = None

    # Content
    description_html: Optional[str] = None
    html_url: Optional[str] = None

    # Quiz-specific
    is_quiz: bool = False
    quiz_id: Optional[int] = None

    # Enriched data (added after syllabus parsing)
    category: Optional[AssignmentCategory] = None
    weight_percentage: Optional[float] = None  # e.g., 0.30 for 30%
    syllabus_due_date: Optional[datetime] = None  # If syllabus has different date

    # Metadata
    source: DataSource = DataSource.CANVAS_API
    confidence: float = 1.0  # 0-1, lower if data is inferred

    @classmethod
    def from_canvas_api(cls, data: dict) -> "Assignment":
        """Parse assignment from Canvas API response."""

        def parse_dt(val: Optional[str]) -> Optional[datetime]:
            if not val:
                return None
            # Canvas uses ISO 8601 format
            return datetime.fromisoformat(val.replace("Z", "+00:00"))

        submission_types = []
        for st in data.get("submission_types", []):
            try:
                submission_types.append(SubmissionType(st))
            except ValueError:
                pass  # Unknown submission type

        return cls(
            id=data["id"],
            course_id=data["course_id"],
            name=data.get("name", "Untitled"),
            due_at=parse_dt(data.get("due_at")),
            unlock_at=parse_dt(data.get("unlock_at")),
            lock_at=parse_dt(data.get("lock_at")),
            created_at=parse_dt(data.get("created_at")),
            updated_at=parse_dt(data.get("updated_at")),
            points_possible=data.get("points_possible"),
            grading_type=data.get("grading_type", "points"),
            omit_from_final_grade=data.get("omit_from_final_grade", False),
            submission_types=submission_types,
            has_submitted=data.get("has_submitted_submissions", False),
            is_locked=data.get("locked_for_user", False),
            lock_explanation=data.get("lock_explanation"),
            description_html=data.get("description"),
            html_url=data.get("html_url"),
            is_quiz=data.get("is_quiz_assignment", False),
            quiz_id=data.get("quiz_id"),
        )


@dataclass
class TodoItem:
    """A todo item from Canvas (wraps an assignment with context)."""
    course_id: int
    course_name: str
    assignment: Assignment
    html_url: str
    todo_type: str = "submitting"  # submitting, grading

    @classmethod
    def from_canvas_api(cls, data: dict) -> "TodoItem":
        """Parse todo item from Canvas /users/self/todo endpoint."""
        assignment_data = data.get("assignment", {})
        assignment = Assignment.from_canvas_api(assignment_data)

        return cls(
            course_id=data["course_id"],
            course_name=data.get("context_name", ""),
            assignment=assignment,
            html_url=data.get("html_url", assignment.html_url or ""),
            todo_type=data.get("type", "submitting"),
        )


# =============================================================================
# Syllabus Models
# =============================================================================

@dataclass
class GradingCategory:
    """A grading category from the syllabus (e.g., 'Homework 30%')."""
    name: str
    weight: float  # 0.0 to 1.0 (e.g., 0.30 for 30%)
    assignment_category: AssignmentCategory = AssignmentCategory.OTHER
    drop_lowest: int = 0  # Number of lowest grades dropped

    # Metadata
    source: DataSource = DataSource.SYLLABUS_HTML
    confidence: float = 1.0


@dataclass
class LatePolicy:
    """Late submission policy parsed from syllabus."""
    allows_late: bool = True
    penalty_per_day: Optional[float] = None  # e.g., 0.10 for 10% per day
    penalty_type: str = "percentage"  # "percentage", "points", "flat"
    max_days_late: Optional[int] = None
    grace_period_hours: int = 0

    # Raw text for LLM context
    raw_text: Optional[str] = None

    source: DataSource = DataSource.SYLLABUS_HTML
    confidence: float = 1.0


@dataclass
class AttendancePolicy:
    """Attendance policy from syllabus."""
    required: bool = False
    affects_grade: bool = False
    max_absences: Optional[int] = None
    penalty_per_absence: Optional[float] = None

    raw_text: Optional[str] = None
    source: DataSource = DataSource.SYLLABUS_HTML
    confidence: float = 1.0


@dataclass
class SyllabusDate:
    """A date/deadline mentioned in the syllabus."""
    date: datetime
    description: str
    assignment_name: Optional[str] = None  # If it matches a Canvas assignment
    event_type: str = "deadline"  # deadline, exam, holiday, etc.

    # For conflict resolution with Canvas
    canvas_assignment_id: Optional[int] = None
    conflicts_with_canvas: bool = False

    source: DataSource = DataSource.SYLLABUS_HTML
    confidence: float = 1.0


@dataclass
class SyllabusInfo:
    """Parsed syllabus information for a course."""
    course_id: int

    # Grading structure
    grading_categories: list[GradingCategory] = field(default_factory=list)
    grading_scale: dict[str, float] = field(default_factory=dict)  # e.g., {"A": 0.90, "B": 0.80}

    # Policies
    late_policy: Optional[LatePolicy] = None
    attendance_policy: Optional[AttendancePolicy] = None

    # Dates from syllabus
    important_dates: list[SyllabusDate] = field(default_factory=list)

    # Raw content for LLM context
    raw_html: Optional[str] = None
    raw_pdf_text: Optional[str] = None

    # Metadata
    source: DataSource = DataSource.SYLLABUS_HTML
    parsed_at: Optional[datetime] = None

    # Timezone assumption for dates in syllabus (default PST)
    assumed_timezone: str = "America/Los_Angeles"

    def get_weight_for_category(self, category: AssignmentCategory) -> Optional[float]:
        """Get the grading weight for an assignment category."""
        for gc in self.grading_categories:
            if gc.assignment_category == category:
                return gc.weight
        return None


# =============================================================================
# Aggregated Student Data
# =============================================================================

@dataclass
class StudentData:
    """All aggregated data for a student - the main container."""

    courses: dict[int, Course] = field(default_factory=dict)  # course_id -> Course
    assignments: dict[int, Assignment] = field(default_factory=dict)  # assignment_id -> Assignment

    # Current todos (subset of assignments)
    todo_items: list[TodoItem] = field(default_factory=list)

    # Metadata
    last_fetched: Optional[datetime] = None

    def add_course(self, course: Course) -> None:
        """Add or update a course."""
        self.courses[course.id] = course

    def add_assignment(self, assignment: Assignment) -> None:
        """Add or update an assignment."""
        self.assignments[assignment.id] = assignment

    def get_assignments_for_course(self, course_id: int) -> list[Assignment]:
        """Get all assignments for a course."""
        return [a for a in self.assignments.values() if a.course_id == course_id]

    def get_upcoming_assignments(self, days: int = 7) -> list[Assignment]:
        """Get assignments due within N days."""
        from datetime import timezone, timedelta
        now = datetime.now(timezone.utc)
        cutoff = now + timedelta(days=days)

        return [
            a for a in self.assignments.values()
            if a.due_at and now <= a.due_at <= cutoff
        ]

    def to_dict(self) -> dict:
        """Serialize to dictionary for LLM context or storage."""
        return {
            "courses": [
                {
                    "id": c.id,
                    "code": c.code,
                    "name": c.name,
                    "html_url": c.html_url,
                    "has_syllabus": c.syllabus is not None,
                    "grading_weights": {
                        gc.assignment_category.value: gc.weight
                        for gc in (c.syllabus.grading_categories if c.syllabus else [])
                    } if c.syllabus else {},
                }
                for c in self.courses.values()
            ],
            "assignments": [
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
                }
                for a in self.assignments.values()
            ],
            "todo_items": [
                {
                    "assignment_id": t.assignment.id,
                    "assignment_name": t.assignment.name,
                    "course_id": t.course_id,
                    "todo_type": t.todo_type,
                    "html_url": t.html_url,
                }
                for t in self.todo_items
            ],
            "last_fetched": self.last_fetched.isoformat() if self.last_fetched else None,
        }

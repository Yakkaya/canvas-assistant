"""
Parsers for Canvas data and syllabi.

Includes:
- Canvas API response parsing
- Syllabus HTML parsing
- Syllabus PDF extraction (stub - requires pdfminer.six or PyMuPDF)
- Assignment category inference
"""

import os
import re
from datetime import datetime, timezone
from typing import Optional

import fitz  # PyMuPDF
from bs4 import BeautifulSoup

from models import (
    Assignment,
    AssignmentCategory,
    Course,
    DataSource,
    GradingCategory,
    LatePolicy,
    StudentData,
    SyllabusDate,
    SyllabusInfo,
    TodoItem,
)


# =============================================================================
# Canvas API Parsing
# =============================================================================

def parse_course(data: dict) -> Course:
    """Parse a course from the Canvas /courses endpoint response."""
    course_id = data["id"]
    name = data.get("name", "")
    # Build a short code from the course_code field (e.g. "CSC-364-05-2262" -> "CSC-364")
    raw_code = data.get("course_code", "")
    parts = raw_code.split("-")
    if len(parts) >= 2:
        code = f"{parts[0]}-{parts[1]}"
    else:
        code = raw_code

    html_url = data.get("html_url") or (
        f"{os.environ.get('CANVAS_BASE_URL', '').rstrip('/')}/courses/{course_id}"
    )
    return Course(id=course_id, name=name, code=code, html_url=html_url)


def parse_assignment(data: dict) -> Assignment:
    """Parse an assignment from the Canvas /courses/{id}/assignments endpoint response."""
    return Assignment.from_canvas_api(data)


def parse_todo_response(todo_json: list[dict]) -> StudentData:
    """Parse the /users/self/todo endpoint response into StudentData."""
    data = StudentData(last_fetched=datetime.now(timezone.utc))

    for item in todo_json:
        todo = TodoItem.from_canvas_api(item)

        # Extract course
        course = Course.from_context_name(
            course_id=todo.course_id,
            context_name=todo.course_name
        )

        # Derive course URL from assignment html_url
        # e.g., "https://canvas.calpoly.edu/courses/172956/assignments/..." -> "https://canvas.calpoly.edu/courses/172956"
        if not course.html_url and todo.assignment.html_url:
            parts = todo.assignment.html_url.split("/courses/")
            if len(parts) == 2:
                course_path = parts[1].split("/")[0]
                course.html_url = f"{parts[0]}/courses/{course_path}"

        data.add_course(course)

        # Add assignment
        data.add_assignment(todo.assignment)
        data.todo_items.append(todo)

    return data


# =============================================================================
# Assignment Category Inference
# =============================================================================

# Keywords to match assignment names to categories
CATEGORY_PATTERNS: dict[AssignmentCategory, list[str]] = {
    AssignmentCategory.HOMEWORK: [
        r"\bhw\d*\b", r"\bhomework\b", r"\bpset\b", r"\bproblem set\b",
        r"\bassignment\s*\d", r"\bweekly\b"
    ],
    AssignmentCategory.PROJECT: [
        r"\bproject\b", r"\bterm paper\b", r"\bfinal project\b",
        r"\bgroup project\b", r"\bsurvey paper\b"
    ],
    AssignmentCategory.EXAM: [
        r"\bexam\b", r"\btest\b", r"\bfinal\b", r"\bmidterm\b"
    ],
    AssignmentCategory.QUIZ: [
        r"\bquiz\b", r"\brq\d", r"\breading quiz\b"
    ],
    AssignmentCategory.LAB: [
        r"\blab\b", r"\blaboratory\b", r"\bexercise\b"
    ],
    AssignmentCategory.PARTICIPATION: [
        r"\bparticipation\b", r"\battendance\b", r"\bin-class\b",
        r"\bactivity\b"
    ],
    AssignmentCategory.READING: [
        r"\breading\b", r"\bresponse\b", r"\breflection\b"
    ],
    AssignmentCategory.DISCUSSION: [
        r"\bdiscussion\b", r"\bforum\b", r"\bpost\b"
    ],
}


def infer_assignment_category(name: str) -> tuple[AssignmentCategory, float]:
    """Infer category from assignment name. Returns (category, confidence)."""
    name_lower = name.lower()

    for category, patterns in CATEGORY_PATTERNS.items():
        for pattern in patterns:
            if re.search(pattern, name_lower):
                return category, 0.8  # High confidence on keyword match

    return AssignmentCategory.OTHER, 0.3  # Low confidence default


def enrich_assignment_categories(data: StudentData) -> None:
    """Add inferred categories to all assignments."""
    for assignment in data.assignments.values():
        if assignment.category is None:
            category, confidence = infer_assignment_category(assignment.name)
            assignment.category = category
            assignment.confidence = min(assignment.confidence, confidence)


# =============================================================================
# Syllabus HTML Parsing
# =============================================================================

def parse_syllabus_html(html: str, course_id: int) -> SyllabusInfo:
    """
    Parse syllabus from Canvas syllabus_body HTML.

    This is a best-effort parser - syllabi vary widely in format.
    Returns partial data with confidence scores.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n", strip=True)

    syllabus = SyllabusInfo(
        course_id=course_id,
        raw_html=html,
        source=DataSource.SYLLABUS_HTML,
        parsed_at=datetime.now(timezone.utc),
    )

    # Try to extract grading weights
    syllabus.grading_categories = _extract_grading_weights(text)

    # Try to extract late policy
    syllabus.late_policy = _extract_late_policy(text)

    # Try to extract dates
    syllabus.important_dates = _extract_dates(text)

    return syllabus


def _extract_grading_weights(text: str) -> list[GradingCategory]:
    """Extract grading weights like 'Homework: 30%' from text."""
    categories: dict[AssignmentCategory, GradingCategory] = {}
    matched_spans: list[tuple[int, int]] = []  # Track (start, end) to avoid double-counting
    text_lower = text.lower()

    # Category keywords - base categories
    category_keywords = (
        r"homework|hw|assignments?|projects?|exams?|quizzes?|labs?|"
        r"participation|attendance|final\s*exam|final|midterm|reading"
    )

    # Extended keywords that include numbered variants like "Exam 1", "Exam 2"
    category_keywords_numbered = (
        r"homework|hw|assignments?|projects?|exams?\s*\d*|quizzes?\s*\d*|labs?\s*\d*|"
        r"participation|attendance|final\s*exam|final|midterm\s*\d*|reading\s*quizzes?"
    )

    # Patterns ordered by reliability (most specific first)
    patterns = [
        # "Exam 1:\n20%" - category (possibly numbered) on one line, X% on next
        rf"({category_keywords_numbered})\s*:?\s*\n\s*(\d+(?:\.\d+)?)\s*%",
        # "Labs (10) 10%" or "Projects (5) 40%" - category with count then percentage
        rf"({category_keywords})(?:\s*\(\d+\))?\s+(\d+(?:\.\d+)?)\s*%",
        # "Homework: 30%" or "Homework 30%"
        rf"({category_keywords})[:\s]+(\d+(?:\.\d+)?)\s*%",
        # "30% Homework" or "30% - Labs" (no newlines allowed between % and category)
        rf"(\d+(?:\.\d+)?)\s*%[ \t]*[-–]?[ \t]*({category_keywords})",
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, text_lower):
            match_start = match.start()

            # Skip if this match overlaps with an existing match
            match_end = match.end()
            if any(not (match_end <= start or match_start >= end) for start, end in matched_spans):
                continue

            groups = match.groups()
            # Handle both orderings (category first or percentage first)
            if groups[0].replace(".", "").isdigit():
                weight_str, name = groups
            else:
                name, weight_str = groups

            weight = float(weight_str) / 100.0

            # Skip "each X%" patterns (per-item weights, not totals)
            # These are typically small (<15%) and preceded by "each"
            match_context = text_lower[max(0, match_start - 20):match_start + len(match.group(0)) + 10]
            if "each" in match_context and weight < 0.15:
                continue

            # Clean up category name (handle "final exam" -> "final", "exam 1" -> "exam")
            name = name.strip()
            # Remove trailing numbers for category mapping (but keep for display)
            base_name = re.sub(r'\s*\d+$', '', name).strip()
            if "final" in base_name and "exam" in base_name:
                base_name = "final"

            category = _name_to_category(base_name)

            # Mark this span as matched
            matched_spans.append((match_start, match_end))

            # Accumulate weights for same category (e.g., Exam 1 + Exam 2 = total exam weight)
            if category in categories:
                categories[category].weight += weight
            else:
                # Use base category name for display (e.g., "Exam" not "Exam 1")
                display_name = base_name.replace("_", " ").title()
                categories[category] = GradingCategory(
                    name=display_name,
                    weight=weight,
                    assignment_category=category,
                    confidence=0.7,
                )

    return list(categories.values())


def _name_to_category(name: str) -> AssignmentCategory:
    """Map grading category name to AssignmentCategory enum."""
    name = name.lower()
    mapping = {
        "homework": AssignmentCategory.HOMEWORK,
        "hw": AssignmentCategory.HOMEWORK,
        "assignment": AssignmentCategory.HOMEWORK,
        "assignments": AssignmentCategory.HOMEWORK,
        "project": AssignmentCategory.PROJECT,
        "projects": AssignmentCategory.PROJECT,
        "exam": AssignmentCategory.EXAM,
        "exams": AssignmentCategory.EXAM,
        "quiz": AssignmentCategory.QUIZ,
        "quizzes": AssignmentCategory.QUIZ,
        "reading quiz": AssignmentCategory.QUIZ,
        "reading quizzes": AssignmentCategory.QUIZ,
        "lab": AssignmentCategory.LAB,
        "labs": AssignmentCategory.LAB,
        "participation": AssignmentCategory.PARTICIPATION,
        "class participation": AssignmentCategory.PARTICIPATION,
        "attendance": AssignmentCategory.PARTICIPATION,
        "final": AssignmentCategory.FINAL,
        "midterm": AssignmentCategory.MIDTERM,
        "reading": AssignmentCategory.READING,
    }
    return mapping.get(name, AssignmentCategory.OTHER)


def _extract_late_policy(text: str) -> Optional[LatePolicy]:
    """Extract late policy from syllabus text."""
    text_lower = text.lower()

    # Check for no late work allowed
    if re.search(r"no late (work|submissions?|assignments?)", text_lower):
        return LatePolicy(
            allows_late=False,
            raw_text=_extract_context(text, "late", 200),
            confidence=0.8,
        )

    # Look for penalty patterns
    penalty_match = re.search(
        r"(\d+(?:\.\d+)?)\s*%?\s*(per day|per hour|each day|daily)",
        text_lower
    )
    if penalty_match:
        penalty = float(penalty_match.group(1))
        if "%" not in penalty_match.group(0):
            penalty = penalty / 100  # Assume percentage

        return LatePolicy(
            allows_late=True,
            penalty_per_day=penalty / 100 if penalty > 1 else penalty,
            penalty_type="percentage",
            raw_text=_extract_context(text, "late", 200),
            confidence=0.6,
        )

    # If "late" mentioned but no clear policy, flag it
    if "late" in text_lower:
        return LatePolicy(
            allows_late=True,  # Assume yes unless explicitly stated
            raw_text=_extract_context(text, "late", 200),
            confidence=0.4,
        )

    return None


def _extract_dates(text: str) -> list[SyllabusDate]:
    """Extract dates from syllabus text."""
    dates = []

    # Common date patterns
    date_patterns = [
        # "January 15" or "Jan 15"
        r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?"
        r"|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
        r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?",
        # "1/15" or "1/15/24"
        r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?",
    ]

    # This is simplified - real implementation would need more context extraction
    # to determine what each date refers to

    return dates  # Return empty for now - this needs more sophisticated parsing


def _extract_context(text: str, keyword: str, chars: int = 200) -> str:
    """Extract text surrounding a keyword for context."""
    text_lower = text.lower()
    idx = text_lower.find(keyword.lower())
    if idx == -1:
        return ""

    start = max(0, idx - chars // 2)
    end = min(len(text), idx + chars // 2)
    return text[start:end].strip()


# =============================================================================
# Syllabus PDF Parsing
# =============================================================================


def extract_text_from_pdf(pdf_source: str | bytes) -> str:
    """
    Extract text from a PDF file path or bytes.

    Args:
        pdf_source: Either a file path (str) or PDF content (bytes)

    Returns:
        Extracted text from all pages
    """
    if isinstance(pdf_source, bytes):
        doc = fitz.open(stream=pdf_source, filetype="pdf")
    else:
        doc = fitz.open(pdf_source)

    text_parts = []
    for page in doc:
        # Extract text with better layout preservation
        text_parts.append(page.get_text("text"))

    doc.close()
    return "\n".join(text_parts)


def extract_tables_from_pdf(pdf_source: str | bytes) -> list[list[list[str]]]:
    """
    Extract tables from PDF (useful for grading breakdowns).

    Returns list of tables, where each table is a list of rows,
    and each row is a list of cell values.
    """
    if isinstance(pdf_source, bytes):
        doc = fitz.open(stream=pdf_source, filetype="pdf")
    else:
        doc = fitz.open(pdf_source)

    tables = []
    for page in doc:
        # PyMuPDF can find tables on pages
        page_tables = page.find_tables()
        for table in page_tables:
            # Extract table data as list of lists
            tables.append(table.extract())

    doc.close()
    return tables


def parse_syllabus_pdf(
    pdf_source: str | bytes,
    course_id: int,
) -> SyllabusInfo:
    """
    Parse syllabus from PDF file or bytes.

    Args:
        pdf_source: File path (str) or PDF content (bytes) from Canvas API
        course_id: The Canvas course ID

    Returns:
        SyllabusInfo with extracted grading weights, policies, dates
    """
    # Extract text
    text = extract_text_from_pdf(pdf_source)

    # Try to extract tables (often contain grading breakdown)
    tables = extract_tables_from_pdf(pdf_source)

    syllabus = SyllabusInfo(
        course_id=course_id,
        raw_pdf_text=text,
        source=DataSource.SYLLABUS_PDF,
        parsed_at=datetime.now(timezone.utc),
    )

    # First try extracting grading from tables (more reliable)
    syllabus.grading_categories = _extract_grading_from_tables(tables)

    # Fall back to text extraction if tables didn't yield results
    if not syllabus.grading_categories:
        syllabus.grading_categories = _extract_grading_weights(text)

    syllabus.late_policy = _extract_late_policy(text)
    syllabus.important_dates = _extract_dates(text)

    return syllabus


def _extract_grading_from_tables(tables: list[list[list[str]]]) -> list[GradingCategory]:
    """
    Extract grading weights from PDF tables.

    Looks for tables with category names and percentages.
    """
    categories = []

    for table in tables:
        for row in table:
            if len(row) < 2:
                continue

            # Look for rows like ["Homework", "30%"] or ["30%", "Homework"]
            for i, cell in enumerate(row):
                if not cell:
                    continue

                cell_lower = cell.lower().strip()

                # Check if this cell is a category name
                category = _name_to_category(cell_lower)
                if category == AssignmentCategory.OTHER:
                    continue

                # Look for percentage in other cells
                for j, other_cell in enumerate(row):
                    if i == j or not other_cell:
                        continue

                    # Try to extract percentage
                    pct_match = re.search(r"(\d+(?:\.\d+)?)\s*%?", other_cell)
                    if pct_match:
                        weight = float(pct_match.group(1))
                        if weight > 1:
                            weight = weight / 100.0

                        # Avoid duplicates
                        if not any(gc.assignment_category == category for gc in categories):
                            categories.append(GradingCategory(
                                name=cell.strip().title(),
                                weight=weight,
                                assignment_category=category,
                                source=DataSource.SYLLABUS_PDF,
                                confidence=0.85,  # Higher confidence from table
                            ))
                        break

    return categories


# =============================================================================
# Conflict Resolution
# =============================================================================

def resolve_date_conflicts(
    assignment: Assignment,
    syllabus_date: SyllabusDate,
) -> Assignment:
    """
    Resolve conflicts between Canvas due date and syllabus date.

    Canvas is treated as ground truth, but we keep syllabus date as metadata.
    """
    if assignment.due_at and syllabus_date.date:
        # Check if dates differ significantly (more than 1 hour)
        diff = abs((assignment.due_at - syllabus_date.date).total_seconds())
        if diff > 3600:  # More than 1 hour difference
            assignment.syllabus_due_date = syllabus_date.date
            syllabus_date.conflicts_with_canvas = True
            syllabus_date.canvas_assignment_id = assignment.id

            # Lower confidence since there's a conflict
            assignment.confidence = min(assignment.confidence, 0.7)

    return assignment


# =============================================================================
# Convenience Functions
# =============================================================================

def load_and_parse_todos(json_data: list[dict]) -> StudentData:
    """Load todos and enrich with category inference."""
    data = parse_todo_response(json_data)
    enrich_assignment_categories(data)
    return data

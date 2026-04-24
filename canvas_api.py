import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("CANVAS_BASE_URL")
TOKEN = os.getenv("CANVAS_TOKEN")

if not BASE_URL or not TOKEN:
    raise RuntimeError("Missing Canvas credentials")

HEADERS = {"Authorization": f"Bearer {TOKEN}"}

def get_user_todo():
    url = f"{BASE_URL.rstrip('/')}/api/v1/users/self/todo"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()


def get_courses() -> list[dict]:
    """Fetch currently active student course enrollments (term not yet ended), handling pagination."""
    from datetime import datetime, timezone
    url = f"{BASE_URL.rstrip('/')}/api/v1/courses"
    params = {
        "enrollment_type": "student",
        "enrollment_state": "active",
        "include[]": "term",
        "per_page": 50,
    }
    now = datetime.now(timezone.utc)
    courses = []
    while url:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        r.raise_for_status()
        for course in r.json():
            term = course.get("term") or {}
            end_at_str = term.get("end_at")
            if end_at_str:
                end_at = datetime.fromisoformat(end_at_str.replace("Z", "+00:00"))
                if end_at < now:
                    continue  # Skip courses from past terms
            courses.append(course)
        url = _next_page(r)
        params = {}
    return courses


def get_course_assignments(course_id: int) -> list[dict]:
    """Fetch all assignments for a course with submission status, handling pagination."""
    url = f"{BASE_URL.rstrip('/')}/api/v1/courses/{course_id}/assignments"
    params = {
        "include[]": "submission",
        "order_by": "due_at",
        "per_page": 100,
    }
    assignments = []
    while url:
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        r.raise_for_status()
        page = r.json()
        # Skip if Canvas returns an error dict (e.g., unauthorized course)
        if isinstance(page, dict):
            break
        assignments.extend(page)
        url = _next_page(r)
        params = {}
    return assignments


def _next_page(response: requests.Response):
    """Extract the next-page URL from a Canvas Link header, or None."""
    link_header = response.headers.get("Link", "")
    for part in link_header.split(","):
        if 'rel="next"' in part:
            url_part = part.split(";")[0].strip()
            return url_part.strip("<>")
    return None


def get_course(course_id: int) -> dict:
    """Fetch course info including syllabus HTML."""
    url = f"{BASE_URL.rstrip('/')}/api/v1/courses/{course_id}"
    params = {"include[]": "syllabus_body"}
    r = requests.get(url, headers=HEADERS, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def get_file(file_id: int) -> bytes:
    """Download a file from Canvas by file ID."""
    # First get file metadata to get the download URL
    url = f"{BASE_URL.rstrip('/')}/api/v1/files/{file_id}"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    file_info = r.json()

    # Download the actual file content
    download_url = file_info.get("url")
    if not download_url:
        raise ValueError(f"No download URL for file {file_id}")

    r = requests.get(download_url, timeout=60)
    r.raise_for_status()
    return r.content
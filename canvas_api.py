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
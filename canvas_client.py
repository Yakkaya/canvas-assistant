import os
import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.getenv("CANVAS_BASE_URL")
TOKEN = os.getenv("CANVAS_TOKEN")

if not BASE_URL or not TOKEN:
    raise SystemExit("Missing CANVAS_BASE_URL or CANVAS_TOKEN in .env")

HEADERS = {"Authorization": f"Bearer {TOKEN}"}

def get_todo():
    url = f"{BASE_URL.rstrip('/')}/api/v1/users/self/todo"
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    return r.json()

def parse_due(item):
    if isinstance(item.get("assignment"), dict):
        return item["assignment"].get("due_at")
    return None

def main():
    items = get_todo()

    print(f"Found {len(items)} to-do items\n")

    for it in items:
        course = it.get("course_id")
        typ = it.get("type")
        title = it.get("title") or (it.get("assignment") or {}).get("name") or "Untitled"
        due = parse_due(it)
        html_url = it.get("html_url") or (it.get("assignment") or {}).get("html_url")

        print(f"- [{typ}] {title}")
        print(f"  course_id: {course}")
        print(f"  due: {due}")
        if html_url:
            print(f"  link: {html_url}")
        print()

if __name__ == "__main__":
    main()



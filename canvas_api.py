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
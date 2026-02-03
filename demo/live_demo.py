"""
Demo: Live Canvas Data + Parsing

This script demonstrates:
1. Fetching live todo data from your Canvas account
2. Parsing syllabus HTML from an external URL
3. Parsing syllabus PDF from Canvas files
4. Combining everything into LLM-ready context with source links

Run: python demo/live_demo.py
"""

import json
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from canvas_api import get_user_todo, get_file
from parsers import load_and_parse_todos, parse_syllabus_html, parse_syllabus_pdf


def demo_live_todos():
    """Fetch and parse live Canvas todo data."""
    print("=" * 60)
    print("1. LIVE CANVAS TODO DATA")
    print("=" * 60)

    print("\nFetching from Canvas API...")
    raw_todos = get_user_todo()
    data = load_and_parse_todos(raw_todos)

    print(f"\nFound {len(data.courses)} courses:")
    for course in data.courses.values():
        print(f"  [{course.code}] {course.name}")
        print(f"    URL: {course.html_url}")

    print(f"\nFound {len(data.assignments)} assignments:")
    for a in sorted(data.assignments.values(), key=lambda x: x.due_at or ""):
        course = data.courses.get(a.course_id)
        status = "✓" if a.has_submitted else "○"
        cat = a.category.value if a.category else "?"
        due = a.due_at.strftime("%b %d %H:%M") if a.due_at else "No due date"

        print(f"\n  {status} [{course.code if course else '?'}] {a.name}")
        print(f"    Due: {due} | Points: {a.points_possible} | Category: {cat}")
        print(f"    Link: {a.html_url}")

    return data


def demo_html_parsing():
    """Parse syllabus from external HTML page."""
    print("\n" + "=" * 60)
    print("2. HTML SYLLABUS PARSING")
    print("=" * 60)

    url = "https://brinckerhoff.org/clements/2248-csc430/"
    print(f"\nFetching: {url}")

    r = requests.get(url, timeout=20)
    syllabus = parse_syllabus_html(r.text, course_id=136484)

    print(f"\nGrading breakdown ({len(syllabus.grading_categories)} categories):")
    total = 0
    for gc in sorted(syllabus.grading_categories, key=lambda x: -x.weight):
        print(f"  {gc.name:<15} {gc.weight*100:>5.0f}%")
        total += gc.weight
    print(f"  {'-'*21}")
    print(f"  {'Total':<15} {total*100:>5.0f}%")

    if syllabus.late_policy:
        lp = syllabus.late_policy
        print(f"\nLate policy: {'Allowed' if lp.allows_late else 'NOT allowed'}")

    return syllabus


def demo_pdf_parsing():
    """Parse syllabus from Canvas PDF file."""
    print("\n" + "=" * 60)
    print("3. PDF SYLLABUS PARSING")
    print("=" * 60)

    file_id = 19627626  # STAT-331 syllabus
    print(f"\nDownloading Canvas file {file_id}...")

    pdf_bytes = get_file(file_id)
    print(f"Downloaded {len(pdf_bytes):,} bytes")

    syllabus = parse_syllabus_pdf(pdf_bytes, course_id=173360)

    print(f"\nGrading breakdown ({len(syllabus.grading_categories)} categories):")
    total = 0
    for gc in sorted(syllabus.grading_categories, key=lambda x: -x.weight):
        print(f"  {gc.name:<15} {gc.weight*100:>5.0f}%")
        total += gc.weight
    print(f"  {'-'*21}")
    print(f"  {'Total':<15} {total*100:>5.0f}%")

    if syllabus.late_policy:
        lp = syllabus.late_policy
        print(f"\nLate policy: {'Allowed' if lp.allows_late else 'NOT allowed'}")

    return syllabus


def demo_llm_context(data):
    """Show the combined data structure for LLM integration."""
    print("\n" + "=" * 60)
    print("4. LLM-READY CONTEXT (to_dict output)")
    print("=" * 60)

    context = data.to_dict()
    print("\n" + json.dumps(context, indent=2, default=str))

    print("\n" + "-" * 60)
    print("EXAMPLE AI RESPONSE:")
    print("-" * 60)

    # Find the next pending assignment
    pending = [a for a in data.assignments.values() if not a.has_submitted]
    if pending:
        next_due = min(pending, key=lambda x: x.due_at or "9999")
        course = data.courses.get(next_due.course_id)

        print(f"""
Based on your Canvas data, you should prioritize:

  "{next_due.name}" for {course.code if course else 'Unknown Course'}
  Due: {next_due.due_at.strftime('%A, %B %d at %I:%M %p') if next_due.due_at else 'Unknown'}
  Worth: {next_due.points_possible} points ({next_due.category.value if next_due.category else 'unknown category'})

  Verify here: {next_due.html_url}
  Course page: {course.html_url if course else 'N/A'}
""")


def main():
    print("\n" + "╔" + "═" * 58 + "╗")
    print("║" + " Canvas Client Demo - Live Data ".center(58) + "║")
    print("╚" + "═" * 58 + "╝")

    # Run demos
    data = demo_live_todos()
    demo_html_parsing()
    demo_pdf_parsing()
    demo_llm_context(data)

    print("=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()

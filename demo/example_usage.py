"""
Demo: Canvas Data Parsing & Syllabus Extraction

This script demonstrates:
1. Parsing Canvas API todo data into structured models
2. Extracting grading weights and policies from syllabus PDFs
3. Combining data for LLM context

Run: python demo/example_usage.py
"""

import json
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from parsers import load_and_parse_todos, parse_syllabus_pdf


def demo_canvas_parsing():
    """Demo 1: Parse Canvas API todo data."""
    print("=" * 60)
    print("DEMO 1: Canvas API Data Parsing")
    print("=" * 60)

    raw_todo_path = Path(__file__).parent / "raw_todo.json"
    if not raw_todo_path.exists():
        print("  [!] raw_todo.json not found. Run demo/get_raw_data.py first.")
        return None

    with open(raw_todo_path) as f:
        raw_todos = json.load(f)

    student_data = load_and_parse_todos(raw_todos)

    print(f"\nLoaded {len(student_data.courses)} courses:")
    for course in student_data.courses.values():
        print(f"  - {course.code}: {course.name}")

    print(f"\nLoaded {len(student_data.assignments)} assignments:")
    for assignment in student_data.assignments.values():
        course = student_data.courses.get(assignment.course_id)
        course_code = course.code if course else "???"

        status = "✓ Submitted" if assignment.has_submitted else "○ Pending"
        category = assignment.category.value if assignment.category else "unknown"

        print(f"\n  [{course_code}] {assignment.name}")
        print(f"    Due: {assignment.due_at}")
        print(f"    Points: {assignment.points_possible} | Category: {category}")
        print(f"    Status: {status}")

    return student_data


def demo_pdf_parsing():
    """Demo 2: Parse syllabus PDFs."""
    print("\n" + "=" * 60)
    print("DEMO 2: Syllabus PDF Parsing")
    print("=" * 60)

    syllabi_dir = Path(__file__).parent.parent / "example_syllabi"
    if not syllabi_dir.exists():
        print(f"  [!] {syllabi_dir}/ folder not found.")
        return []

    pdf_files = [f for f in syllabi_dir.iterdir() if f.suffix == ".pdf"]
    if not pdf_files:
        print(f"  [!] No PDF files found in {syllabi_dir}/")
        return []

    print(f"\nFound {len(pdf_files)} syllabus PDFs:")
    parsed_syllabi = []

    for pdf_path in sorted(pdf_files)[:3]:  # Demo first 3
        pdf_file = pdf_path.name
        print(f"\n  --- {pdf_file} ---")

        syllabus = parse_syllabus_pdf(pdf_path, course_id=0)
        parsed_syllabi.append((pdf_file, syllabus))

        # Show grading breakdown
        if syllabus.grading_categories:
            print("  Grading Breakdown:")
            total = 0
            for gc in sorted(syllabus.grading_categories, key=lambda x: -x.weight):
                print(f"    {gc.name:<20} {gc.weight*100:>5.0f}%")
                total += gc.weight
            print(f"    {'─'*26}")
            print(f"    {'Total':<20} {total*100:>5.0f}%")
        else:
            print("  Grading Breakdown: Not found")

        # Show late policy
        if syllabus.late_policy:
            lp = syllabus.late_policy
            if lp.allows_late:
                print(f"  Late Policy: Allowed (confidence: {lp.confidence}/5)")
            else:
                print(f"  Late Policy: NOT allowed (confidence: {lp.confidence}/5)")
        else:
            print("  Late Policy: Not found")

    return parsed_syllabi


def demo_combined_output():
    """Demo 3: Show combined data structure for LLM."""
    print("\n" + "=" * 60)
    print("DEMO 3: Combined Data for LLM Context")
    print("=" * 60)

    # This shows what data structure would be passed to an LLM
    example_context = {
        "student_question": "What should I prioritize this week?",
        "courses": [
            {
                "code": "CSC-364",
                "name": "Intro to Networked Computing",
                "grading_weights": {
                    "labs": 0.20,
                    "projects": 0.30,
                    "exams": 0.20,
                    "final": 0.30,
                },
                "late_policy": {
                    "allows_late": True,
                    "penalty": "15% per day",
                },
            }
        ],
        "pending_assignments": [
            {
                "name": "Lab 1: Getting Started",
                "course": "CSC-364",
                "due": "2026-01-17T03:00:00Z",
                "points": 10,
                "category": "lab",
                "grade_weight": 0.02,  # 20% for labs / 10 labs
            }
        ],
    }

    print("\nExample LLM context structure:")
    print(json.dumps(example_context, indent=2))

    print("\n" + "-" * 40)
    print("With this context, an LLM could answer:")
    print('  "Lab 1 is worth 2% of your grade and due Thursday.')
    print('   Since CSC-364 allows late submissions with a 15% penalty,')
    print('   you could submit late if needed, but prioritize it')
    print('   given the upcoming deadline."')


def main():
    print("\n" + "╔" + "═" * 58 + "╗")
    print("║" + "  Canvas Student Data Parser - Demo  ".center(58) + "║")
    print("╚" + "═" * 58 + "╝\n")

    # Run demos
    student_data = demo_canvas_parsing()
    parsed_syllabi = demo_pdf_parsing()
    demo_combined_output()

    print("\n" + "=" * 60)
    print("Demo complete!")
    print("=" * 60)


if __name__ == "__main__":
    main()

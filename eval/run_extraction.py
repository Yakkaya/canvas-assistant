"""
Run the syllabus parser over a folder of syllabi and record what it extracted.

Supported inputs:
  .pdf            -> parse_syllabus_pdf (table extraction, falling back to text regex)
  .html / .htm    -> parse_syllabus_html (regex on page text)
  .jpg/.jpeg/.png -> Apple Vision OCR (eval/ocr.swift), then the same text regex
                     used for HTML/PDF text. These are screenshots of web syllabi.

Byte-identical files are parsed once; later copies are recorded as duplicates
and excluded from scoring.

Outputs (in eval/output/):
  extractions.json  one record per file: method, extracted categories, late policy
  ocr_text/         OCR text for each image, for checking OCR quality
  pdf_text/         extracted text for each PDF

Also creates blank ground-truth CSVs in eval/ground_truth/ (never overwrites).

Usage:
  python eval/run_extraction.py [folder]        # default: full_syllabi_set
"""

import csv
import hashlib
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import parsers  # noqa: E402

EVAL_DIR = os.path.join(ROOT, "eval")
OUT_DIR = os.path.join(EVAL_DIR, "output")
GT_DIR = os.path.join(EVAL_DIR, "ground_truth")
OCR_SRC = os.path.join(EVAL_DIR, "ocr.swift")
OCR_BIN = os.path.join(EVAL_DIR, ".bin", "ocr")

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
HTML_EXTS = {".html", ".htm"}
PDF_EXTS = {".pdf"}


def sha256(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def ensure_ocr_binary() -> None:
    if os.path.exists(OCR_BIN) and os.path.getmtime(OCR_BIN) >= os.path.getmtime(OCR_SRC):
        return
    os.makedirs(os.path.dirname(OCR_BIN), exist_ok=True)
    subprocess.run(["swiftc", "-O", OCR_SRC, "-o", OCR_BIN], check=True)


def ocr_text(path: str) -> str:
    """OCR an image, caching the text in eval/output/ocr_text/."""
    cache = os.path.join(OUT_DIR, "ocr_text", os.path.basename(path) + ".txt")
    if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(path):
        with open(cache) as f:
            return f.read()
    text = subprocess.run([OCR_BIN, path], check=True, capture_output=True, text=True).stdout
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "w") as f:
        f.write(text)
    return text


def categories_record(categories) -> list[dict]:
    return [
        {
            "name": gc.name,
            "category": gc.assignment_category.value,
            "weight": round(gc.weight, 6),
            "confidence": gc.confidence,
        }
        for gc in categories
    ]


def late_record(lp) -> dict | None:
    if lp is None:
        return None
    return {
        "allows_late": lp.allows_late,
        "penalty_per_day": lp.penalty_per_day,
        "max_days_late": lp.max_days_late,
        "grace_period_hours": lp.grace_period_hours,
        "confidence": lp.confidence,
        "raw_text": lp.raw_text,
    }


def extract(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower()
    record = {"file": os.path.basename(path)}

    if ext in PDF_EXTS:
        text = parsers.extract_text_from_pdf(path)
        tables = parsers.extract_tables_from_pdf(path)
        table_cats = parsers._extract_grading_from_tables(tables)
        syllabus = parsers.parse_syllabus_pdf(path, course_id=0)
        # parse_syllabus_pdf uses table categories when any exist, else text regex
        record["method"] = "pdf_table" if table_cats else "pdf_text"
        record["tables_found"] = len(tables)
        categories, late = syllabus.grading_categories, syllabus.late_policy
        text_dir = os.path.join(OUT_DIR, "pdf_text")
        os.makedirs(text_dir, exist_ok=True)
        with open(os.path.join(text_dir, record["file"] + ".txt"), "w") as f:
            f.write(text)
    elif ext in HTML_EXTS:
        with open(path, encoding="utf-8", errors="replace") as f:
            html = f.read()
        syllabus = parsers.parse_syllabus_html(html, course_id=0)
        record["method"] = "html"
        text = html
        categories, late = syllabus.grading_categories, syllabus.late_policy
    elif ext in IMAGE_EXTS:
        text = ocr_text(path)
        record["method"] = "image_ocr"
        categories = parsers._extract_grading_weights(text)
        late = parsers._extract_late_policy(text)
    else:
        raise ValueError(f"unsupported file type: {ext}")

    record["text_chars"] = len(text.strip())
    record["grading_categories"] = categories_record(categories)
    record["late_policy"] = late_record(late)
    return record


def write_ground_truth_templates(files: list[str]) -> None:
    os.makedirs(GT_DIR, exist_ok=True)

    cats_path = os.path.join(GT_DIR, "grading_categories.csv")
    if not os.path.exists(cats_path):
        with open(cats_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["file", "category_name", "category", "weight_pct", "notes"])
            for name in files:
                w.writerow([name, "", "", "", ""])

    late_path = os.path.join(GT_DIR, "late_policies.csv")
    if not os.path.exists(late_path):
        with open(late_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow([
                "file", "policy_stated", "allows_late", "penalty_per_day_pct",
                "max_days_late", "grace_period_hours", "notes",
            ])
            for name in files:
                w.writerow([name, "", "", "", "", "", ""])


def main() -> None:
    folder = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "full_syllabi_set"))
    supported = IMAGE_EXTS | HTML_EXTS | PDF_EXTS
    paths = sorted(
        os.path.join(folder, n) for n in os.listdir(folder)
        if os.path.splitext(n)[1].lower() in supported
    )
    os.makedirs(OUT_DIR, exist_ok=True)
    if any(os.path.splitext(p)[1].lower() in IMAGE_EXTS for p in paths):
        ensure_ocr_binary()

    # Keep the plainest name of each duplicate set ("X.pdf" over "X copy.pdf" / "X (1).pdf")
    def name_rank(p: str) -> tuple:
        base = os.path.splitext(os.path.basename(p))[0]
        return (bool(re.search(r" copy$|\(\d+\)$", base)), len(base), base)

    first_by_hash: dict[str, str] = {}
    duplicates: dict[str, str] = {}
    unique: list[str] = []
    for p in sorted(paths, key=name_rank):
        h = sha256(p)
        if h in first_by_hash:
            duplicates[os.path.basename(p)] = first_by_hash[h]
        else:
            first_by_hash[h] = os.path.basename(p)
            unique.append(p)

    def run(p: str) -> dict:
        try:
            return extract(p)
        except Exception as e:
            return {"file": os.path.basename(p), "method": "error", "error": repr(e)}

    # OCR (a subprocess per image) runs concurrently to warm the cache;
    # parsing stays sequential because PyMuPDF is not thread-safe.
    images = [p for p in unique if os.path.splitext(p)[1].lower() in IMAGE_EXTS]
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(ocr_text, images))
    unique.sort()
    records = [run(p) for p in unique]
    for name, original in duplicates.items():
        records.append({"file": name, "method": "duplicate", "duplicate_of": original})
    records.sort(key=lambda r: r["file"])

    with open(os.path.join(OUT_DIR, "extractions.json"), "w") as f:
        json.dump({"folder": folder, "records": records}, f, indent=2)

    write_ground_truth_templates([os.path.basename(p) for p in unique])

    by_method: dict[str, int] = {}
    for r in records:
        by_method[r["method"]] = by_method.get(r["method"], 0) + 1
    print(f"{len(paths)} files, {len(unique)} unique, {len(duplicates)} duplicates")
    print("by method:", by_method)
    print(f"wrote {os.path.relpath(os.path.join(OUT_DIR, 'extractions.json'), ROOT)}")


if __name__ == "__main__":
    main()

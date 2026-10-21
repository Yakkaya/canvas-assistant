"""
Score parser output against hand-labeled ground truth.

Inputs:
  eval/output/extractions.json                  (from run_extraction.py)
  eval/ground_truth/grading_categories.csv      (hand-labeled)
  eval/ground_truth/late_policies.csv           (hand-labeled)
  eval/ground_truth/assignment_categories.csv   (hand-labeled, optional)

Only files/rows that have been labeled are scored; the report says how many
were skipped. Writes eval/output/results.md and prints it.

Matching rules
  Grading categories are compared at the level of the system's category
  taxonomy. Ground-truth rows with the same category in one syllabus are
  summed (e.g. Exam 1 + Exam 2), matching how the parser accumulates them.
  An extracted category is correct if that category is in the ground truth
  and its weight equals the ground-truth weight to within 0.05 percentage
  points (i.e. exact up to rounding).

  An extracted late policy is correct if the syllabus states a policy and
  allows_late, penalty per day, max days late, and grace period all match
  (blank in the ground truth means "not stated"). A syllabus with no stated
  policy for which the parser returns nothing is a correct rejection.

Usage:
  python eval/score.py
"""

import csv
import json
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from parsers import infer_assignment_category  # noqa: E402

OUT_DIR = os.path.join(ROOT, "eval", "output")
GT_DIR = os.getenv("EVAL_GT_DIR", os.path.join(ROOT, "eval", "ground_truth"))
WEIGHT_TOL = 0.0005  # 0.05 percentage points

METHOD_LABELS = {
    "pdf_table": "PDF table",
    "pdf_text": "PDF text (regex)",
    "html": "HTML (regex)",
    "image_ocr": "Web page screenshot (OCR + regex)",
}


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.1f}%" if d else "—"


def yes(value: str) -> bool | None:
    v = value.strip().lower()
    if v in ("y", "yes", "true", "1"):
        return True
    if v in ("n", "no", "false", "0"):
        return False
    return None


def num(value: str) -> float | None:
    v = value.strip().rstrip("%")
    return float(v) if v else None


def read_csv(name: str) -> list[dict]:
    path = os.path.join(GT_DIR, name)
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(str(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Grading categories
# ---------------------------------------------------------------------------

def load_category_truth() -> dict[str, dict[str, float]]:
    """file -> {category: summed weight (0-1)}. Files with only blank rows are unlabeled."""
    truth: dict[str, dict[str, float]] = {}
    for row in read_csv("grading_categories.csv"):
        name = row["category_name"].strip()
        if not name:
            continue
        cats = truth.setdefault(row["file"], {})
        if name.upper() == "NONE":  # syllabus states no weighted breakdown
            continue
        cat = row["category"].strip().lower() or "other"
        cats[cat] = cats.get(cat, 0.0) + (num(row["weight_pct"]) or 0.0) / 100
    return truth


def score_categories(records: list[dict], truth: dict) -> tuple[str, dict]:
    by_method = defaultdict(Counter)
    by_conf = defaultdict(Counter)
    failures = []

    for r in records:
        if r["file"] not in truth:
            continue
        gt = truth[r["file"]]
        m = by_method[r["method"]]
        m["syllabi"] += 1
        m["truth"] += len(gt)
        found = set()
        for c in r["grading_categories"]:
            m["extracted"] += 1
            by_conf[c["confidence"]]["extracted"] += 1
            if c["category"] in gt and abs(c["weight"] - gt[c["category"]]) < WEIGHT_TOL:
                m["correct"] += 1
                by_conf[c["confidence"]]["correct"] += 1
                found.add(c["category"])
            elif c["category"] in gt:
                m["wrong_weight"] += 1
                failures.append((r["file"], "wrong weight",
                                 f"{c['category']}: got {c['weight']*100:g}%, truth {gt[c['category']]*100:g}%"))
            else:
                m["spurious"] += 1
                failures.append((r["file"], "not in syllabus", f"{c['category']} {c['weight']*100:g}% ('{c['name']}')"))
        for cat in gt:
            extracted_cats = {c["category"] for c in r["grading_categories"]}
            if cat not in extracted_cats:
                m["missed"] += 1
                failures.append((r["file"], "missed", f"{cat} {gt[cat]*100:g}%"))
        if gt and found == set(gt) and len(r["grading_categories"]) == len(gt):
            m["fully_correct"] += 1

    methods = [k for k in METHOD_LABELS if k in by_method]
    total = sum(by_method.values(), Counter())
    rows = []
    for k in methods + ["__total__"]:
        c = total if k == "__total__" else by_method[k]
        rows.append([
            "**Overall**" if k == "__total__" else METHOD_LABELS[k],
            c["syllabi"], c["truth"], c["extracted"], c["correct"],
            pct(c["correct"], c["extracted"]), pct(c["correct"], c["truth"]),
            f"{c['fully_correct']}/{c['syllabi']}",
        ])
    out = [table(
        ["Extraction method", "Syllabi", "Ground-truth categories", "Values extracted", "Correct",
         "Precision (correct / extracted)", "Recall (correct / ground truth)", "Syllabi fully correct"],
        rows,
    )]
    out.append("")
    out.append(f"Failure breakdown: {total['wrong_weight']} right category with wrong weight, "
               f"{total['spurious']} categories not in the syllabus, {total['missed']} ground-truth categories missed.")
    return "\n".join(out), {"by_conf": by_conf, "failures": failures}


# ---------------------------------------------------------------------------
# Late policies
# ---------------------------------------------------------------------------

def late_matches(pred: dict, gt: dict) -> bool:
    if pred["allows_late"] != gt["allows_late"]:
        return False
    gp, pp = gt["penalty"], pred["penalty_per_day"]
    if (gp is None) != (pp is None) or (gp is not None and abs(gp / 100 - pp) >= WEIGHT_TOL):
        return False
    if gt["max_days"] != (float(pred["max_days_late"]) if pred["max_days_late"] is not None else None):
        return False
    return (gt["grace"] or 0) == (pred["grace_period_hours"] or 0)


def score_late(records: list[dict]) -> tuple[str, Counter, list]:
    truth = {}
    for row in read_csv("late_policies.csv"):
        stated = yes(row["policy_stated"])
        if stated is None:
            continue
        truth[row["file"]] = {
            "stated": stated,
            "allows_late": yes(row["allows_late"]) if stated else None,
            "penalty": num(row["penalty_per_day_pct"]),
            "max_days": num(row["max_days_late"]),
            "grace": num(row["grace_period_hours"]),
        }

    c = Counter()
    by_conf = defaultdict(Counter)
    failures = []
    for r in records:
        gt = truth.get(r["file"])
        if gt is None:
            continue
        pred = r["late_policy"]
        c["syllabi"] += 1
        c["stated"] += gt["stated"]
        if pred is None:
            if gt["stated"]:
                c["missed"] += 1
                failures.append((r["file"], "late policy missed", ""))
            else:
                c["correct_rejection"] += 1
            continue
        c["extracted"] += 1
        by_conf[pred["confidence"]]["extracted"] += 1
        if gt["stated"] and late_matches(pred, gt):
            c["correct"] += 1
            by_conf[pred["confidence"]]["correct"] += 1
        else:
            why = "no policy in syllabus" if not gt["stated"] else (
                f"got allows_late={pred['allows_late']}, penalty={pred['penalty_per_day']}, "
                f"max_days={pred['max_days_late']}; truth allows_late={gt['allows_late']}, "
                f"penalty={gt['penalty']}%, max_days={gt['max_days']}")
            failures.append((r["file"], f"late policy wrong (conf {pred['confidence']})", why))

    out = table(
        ["Syllabi", "Policies stated", "Policies extracted", "Correct", "Precision", "Recall",
         "Correctly reported no policy"],
        [[c["syllabi"], c["stated"], c["extracted"], c["correct"],
          pct(c["correct"], c["extracted"]), pct(c["correct"], c["stated"]),
          f"{c['correct_rejection']}/{c['syllabi'] - c['stated']}"]],
    )
    return out, by_conf, failures


def confidence_table(by_conf: dict) -> str:
    rows = []
    for level in sorted(by_conf, reverse=True):
        cc = by_conf[level]
        rows.append([level, cc["extracted"], cc["correct"], pct(cc["correct"], cc["extracted"])])
    return table(["Confidence", "Values", "Correct", "Accuracy"], rows) if rows else "_No labeled values._"


# ---------------------------------------------------------------------------
# Assignment categorization
# ---------------------------------------------------------------------------

def score_assignments() -> str:
    rows = [r for r in read_csv("assignment_categories.csv") if r.get("label", "").strip()]
    if not rows:
        return "_No labeled assignment names yet (eval/ground_truth/assignment_categories.csv)._"
    confusion = Counter()
    errors = []
    correct = 0
    for r in rows:
        label = r["label"].strip().lower()
        pred, _ = infer_assignment_category(r["assignment_name"])
        confusion[(label, pred.value)] += 1
        if pred.value == label:
            correct += 1
        else:
            errors.append([r.get("course", ""), r["assignment_name"], label, pred.value])
    courses = len({r.get("course", "") for r in rows})
    out = [f"Correct: **{correct} of {len(rows)}** ({pct(correct, len(rows))}) across {courses} courses.", ""]

    per_label = Counter(label for label, _ in confusion.elements())
    out.append(table(
        ["True category", "Names", "Correct", "Accuracy", "Most common wrong prediction"],
        [[label, n, confusion[(label, label)], pct(confusion[(label, label)], n),
          next((f"{p} ({k})" for (l, p), k in confusion.most_common() if l == label and p != label), "—")]
         for label, n in per_label.most_common()],
    ))
    if errors:
        out += ["", "<details><summary>Misclassified names</summary>", "",
                table(["Course", "Assignment", "Label", "Predicted"], errors), "", "</details>"]
    return "\n".join(out)


# ---------------------------------------------------------------------------

def main() -> None:
    with open(os.path.join(OUT_DIR, "extractions.json")) as f:
        all_records = json.load(f)["records"]
    records = [r for r in all_records if r["method"] not in ("duplicate", "error")]
    truth = load_category_truth()

    cat_table, cat_info = score_categories(records, truth)
    late_table, late_conf, late_failures = score_late(records)

    report = [
        "# Evaluation results",
        "",
        f"{len(records)} unique syllabi parsed "
        f"({sum(1 for r in all_records if r['method'] == 'duplicate')} byte-identical duplicates excluded). "
        f"Grading categories labeled for {sum(1 for r in records if r['file'] in truth)}.",
        "",
        "## Syllabus extraction: grading categories",
        "",
        cat_table,
        "",
        "## Syllabus extraction: late policies",
        "",
        late_table,
        "",
        "## Accuracy by confidence score",
        "",
        "Grading categories:",
        "",
        confidence_table(cat_info["by_conf"]),
        "",
        "Late policies:",
        "",
        confidence_table(late_conf),
        "",
        "## Assignment categorization",
        "",
        score_assignments(),
        "",
        "## Failures",
        "",
    ]
    failures = sorted(cat_info["failures"] + late_failures)
    report.append(table(["File", "Failure", "Detail"], [list(f) for f in failures]) if failures else "_None._")

    text = "\n".join(report) + "\n"
    # Labels from another folder (EVAL_GT_DIR, e.g. a test fixture) never overwrite the real results
    name = "results.md" if "EVAL_GT_DIR" not in os.environ else "results_custom_labels.md"
    with open(os.path.join(OUT_DIR, name), "w") as f:
        f.write(text)
    print(text)


if __name__ == "__main__":
    main()

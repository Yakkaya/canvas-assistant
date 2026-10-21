# Evaluation

Scripts for measuring syllabus extraction, assignment categorization, and agent
behavior. Run everything from the repository root with the project venv.

```
python eval/run_extraction.py [folder]        # parse every syllabus (default: full_syllabi_set/)
python eval/export_assignments.py --offline   # assignment names saved locally (no Canvas token needed)
python eval/score.py                          # compare against labels -> eval/output/results.md

python eval/agent_fixture.py                  # seed the agent test database (re-run right before each agent run)
python eval/run_agent_eval.py --provider gemini   # 12 fixed questions -> eval/output/agent_eval_*.md
```

The syllabi, everything derived from them (`eval/output/`), and the
ground-truth labels (`eval/ground_truth/`) are not in the repository: the
syllabi are instructors' documents, and the labels quote them. Put your own
syllabi in a folder and pass it to `run_extraction.py`; `agent_fixture.py`
expects the specific Cal Poly syllabi listed in its `COURSES` table, so edit
that table to point at syllabi you have.

None of these need Canvas access. `export_assignments.py` without `--offline`
pulls names live from Canvas and needs a valid `CANVAS_TOKEN`.

## Agent evaluation

`agent_fixture.py` seeds a separate database (`canvas_assistant_eval`; your
real data is untouched) with five courses whose syllabi are real PDFs from
`full_syllabi_set/`, parsed by the actual parser, chosen to span clean,
partial, inconsistent, and empty extractions plus one student correction.
Assignments are synthetic, dated relative to the time of seeding, and
categorized by the real classifier. Because dates are relative, seed again
right before each run.

`run_agent_eval.py` sends each question in `agent_questions.json` in a fresh
session and logs every tool call and result. With `--provider gemini` it runs
the production code path (`web_server.run_chat_turn`). The report gives
automatic checks (expected tools called, cited percentages present in tool
results, uncertainty expressed, source named) plus full transcripts for manual
review. To try the widget against the same data (e.g. for a figure):
`MONGO_DB_NAME=canvas_assistant_eval uvicorn web_server:app --port 8000`.

`run_extraction.py` handles PDFs (table extraction, falling back to text regex),
saved HTML pages, and page screenshots (`.jpeg`/`.png`), which are OCR'd with
Apple's Vision framework (`ocr.swift`, compiled on first run; macOS only) and
then run through the same text regex. OCR and PDF text are saved under
`eval/output/` so extraction errors can be traced to OCR versus parsing.
Byte-identical files are parsed once.

## Labeling

Label from the syllabus itself, not from the parser output. Only labeled rows
are scored, so you can label incrementally and re-run `score.py`.

### `ground_truth/grading_categories.csv`

One row per graded component exactly as the syllabus states it. Copy the row
for additional components.

| column | value |
|---|---|
| `category_name` | as written ("Midterm 1", "Programming Assignments"); `NONE` if the syllabus gives no weighted breakdown |
| `category` | one of `homework project exam quiz lab participation reading discussion final midterm other` — use `final` for the final exam, `midterm` for midterms, `exam` for other exams, `homework` for "assignments", `other` for anything that fits none |
| `weight_pct` | the component's total weight in percent (`30`, not `0.30`) |

Rows sharing a `category` in one syllabus are summed before comparison
(Exam 1 20% + Exam 2 20% = exam 40%), matching how the parser combines them.
An extracted category counts as correct only if its weight matches to within
0.05 percentage points.

### `ground_truth/late_policies.csv`

One row per syllabus.

| column | value |
|---|---|
| `policy_stated` | `yes` / `no` |
| `allows_late` | `yes` / `no` (blank if no policy) |
| `penalty_per_day_pct` | e.g. `10`; blank if none stated |
| `max_days_late` | blank if none stated |
| `grace_period_hours` | blank if none stated |

### `ground_truth/assignment_categories.csv`

Fill `label` with one of `homework project exam quiz lab participation reading
discussion other`. The name classifier never outputs `midterm` or `final`
(those names match `exam` first), so label midterms and finals as `exam`.

## Agent logs

`web_server.py` appends every chat turn to `eval/logs/agent_turns.jsonl`
(message, each tool call with arguments and full result, reply, duration).
Comparing a reply against the tool results logged in the same turn is how
citation accuracy and uncertainty reporting can be checked. The log contains
your Canvas data and is gitignored.

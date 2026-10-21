"""
Run a fixed question set through the agent against the seeded test database.

Each question runs in a fresh session. Every turn (question, tool calls with
arguments and full results, reply) is logged, then automatically checked:

  tools      did the agent call every tool the question requires?
  grounded   does every percentage in the reply appear in that turn's tool
             results? (derived numbers, e.g. 2 days x 10% = 20%, are flagged
             for manual review rather than counted as errors)
  hedged     did the reply express uncertainty, for questions whose data is
             low-confidence or visibly inconsistent?
  sourced    did the reply attribute the value to the expected source?

The automatic checks are aids; the full transcripts in the report are what a
human reviewer should judge.

Providers:
  gemini  runs the production path, web_server.run_chat_turn (model from
          --model or GEMINI_MODEL, default gemini-2.5-flash)
  openai  same tools and system prompt, OpenAI chat-completions tool loop

Usage:
  python eval/agent_fixture.py                       # seed first
  python eval/run_agent_eval.py --provider gemini
  python eval/run_agent_eval.py --provider openai [--model gpt-4o] [--only Q4,Q8]
"""

import argparse
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

EVAL_DB = "canvas_assistant_eval"
os.environ.setdefault("MONGO_DB_NAME", EVAL_DB)  # before any MongoStore is created

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(ROOT, ".env"))

from db import MongoStore  # noqa: E402
from prompts import SYSTEM_PROMPT, stamp_message  # noqa: E402
from tools import TOOL_SCHEMAS, execute_tool  # noqa: E402

OUT_DIR = os.path.join(ROOT, "eval", "output")
QUESTIONS = os.path.join(ROOT, "eval", "agent_questions.json")
STUDENT_ID = "self"

HEDGE = re.compile(
    r"may not be (fully )?accurate|might not be|not (fully )?reliable|uncertain|low confidence|"
    r"confidence|verify|double[- ]check|confirm|inconsistent|doesn't add up|don't add up|"
    r"does not add up|adds? up to|only (adds|sums|accounts)|incomplete|missing|unclear|not sure",
    re.IGNORECASE,
)
SOURCE_PHRASES = {
    "syllabus pdf": re.compile(r"syllabus pdf|pdf syllabus|syllabus \(pdf\)", re.IGNORECASE),
    "course syllabus": re.compile(r"course syllabus|from the syllabus", re.IGNORECASE),
    "your correction": re.compile(r"your (own )?correction|you (entered|corrected|updated)|your update", re.IGNORECASE),
    "canvas": re.compile(r"from canvas", re.IGNORECASE),
}


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

TRANSIENT = re.compile(r"\b(429|500|503)\b|UNAVAILABLE|RESOURCE_EXHAUSTED|high demand")
DAILY_QUOTA = re.compile(r"PerDay|\b402\b|prepayment credits")  # not fixed by retrying


class DailyQuotaExceeded(Exception):
    pass


def logged_requests(log_path: str) -> int:
    """Model API requests made so far, counting a failed call as a request."""
    n = 0
    if os.path.exists(log_path):
        with open(log_path) as f:
            for line in f:
                rec = json.loads(line)
                n += rec.get("model_rounds", 0) + (1 if rec.get("error") else 0)
    return n


def logged_cost(log_path: str, price_in: float, price_out: float) -> tuple[int, int, float]:
    """Tokens and estimated USD for every logged attempt, including failed ones."""
    tin = tout = 0
    if os.path.exists(log_path):
        with open(log_path) as f:
            for line in f:
                u = json.loads(line).get("usage") or {}
                tin += u.get("input_tokens", 0)
                tout += u.get("output_tokens", 0)
    return tin, tout, tin / 1e6 * price_in + tout / 1e6 * price_out
RETRY_DELAYS = [15, 45, 90]  # seconds


def run_gemini(question: str, log_path: str) -> dict:
    """Ask in a fresh session; retry from scratch on transient API errors.

    Every attempt is logged; the returned record notes how many failed
    attempts preceded it.
    """
    os.environ["AGENT_LOG_PATH"] = log_path
    import web_server  # noqa: E402  (reads AGENT_LOG_PATH and MONGO_DB_NAME at import)

    web_server.AGENT_LOG_PATH = log_path
    failed = []
    for delay in RETRY_DELAYS + [None]:
        try:
            web_server.run_chat_turn(f"eval-{uuid.uuid4()}", question)
        except Exception:
            pass  # the failed turn is still logged
        with open(log_path) as f:
            record = json.loads(f.readlines()[-1])
        if record.get("error") and DAILY_QUOTA.search(record["error"]):
            raise DailyQuotaExceeded(record["error"][:300])
        if not (record.get("error") and TRANSIENT.search(record["error"])) or delay is None:
            record["failed_attempts"] = failed
            return record
        failed.append(record["error"][:120])
        print(f"    transient error, retrying in {delay}s", flush=True)
        time.sleep(delay)


def run_openai(question: str, log_path: str, store: MongoStore, model: str) -> dict:
    from openai import OpenAI

    client = OpenAI()
    tools = [{"type": "function", "function": t} for t in TOOL_SCHEMAS]
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": stamp_message(question)},
    ]
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": f"eval-{uuid.uuid4()}",
        "new_session": True,
        "model": model,
        "message": question,
        "tool_calls": [],
        "model_rounds": 0,
        "reply": None,
        "error": None,
    }
    started = time.monotonic()
    try:
        for _ in range(10):
            response = client.chat.completions.create(model=model, messages=messages, tools=tools)
            record["model_rounds"] += 1
            msg = response.choices[0].message
            if not msg.tool_calls:
                record["reply"] = (msg.content or "").strip()
                break
            messages.append(msg)
            for tc in msg.tool_calls:
                args = json.loads(tc.function.arguments or "{}")
                try:
                    result = execute_tool(store, STUDENT_ID, tc.function.name, args)
                except Exception as e:
                    result = json.dumps({"error": str(e)})
                record["tool_calls"].append({
                    "round": record["model_rounds"], "name": tc.function.name, "args": args, "result": result,
                })
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
        else:
            record["error"] = "tool loop limit reached"
    except Exception as e:
        record["error"] = repr(e)
    record["duration_s"] = round(time.monotonic() - started, 2)
    with open(log_path, "a") as f:
        f.write(json.dumps(record, default=str) + "\n")
    return record


# ---------------------------------------------------------------------------
# Automatic checks
# ---------------------------------------------------------------------------

def percentages_in_results(record: dict) -> set[float]:
    """Every percentage the tools returned: weights, weight_pct strings, late penalties."""
    found: set[float] = set()

    def walk(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("weight", "penalty_per_day") and isinstance(v, (int, float)):
                    found.add(round(v * 100, 2))
                elif isinstance(v, str):
                    found.update(round(float(m), 2) for m in re.findall(r"(\d+(?:\.\d+)?)\s*%", v))
                else:
                    walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    for tc in record["tool_calls"]:
        try:
            walk(json.loads(tc["result"]))
        except json.JSONDecodeError:
            pass
    return found


def check(q: dict, record: dict) -> dict:
    reply = record.get("reply") or ""
    called = [tc["name"] for tc in record["tool_calls"]]
    missing_tools = [t for t in q.get("expect_tools", []) if t not in called]
    cited = {round(float(p), 2) for p in re.findall(r"(\d+(?:\.\d+)?)\s*%", reply)}
    ungrounded = sorted(cited - percentages_in_results(record))
    result = {
        "tools_ok": not missing_tools,
        "missing_tools": missing_tools,
        "called": called,
        "cited_pct": sorted(cited),
        "ungrounded_pct": ungrounded,
    }
    if q.get("expect_uncertainty"):
        result["hedged"] = bool(HEDGE.search(reply))
    if q.get("expect_source"):
        result["sourced"] = any(SOURCE_PHRASES[s].search(reply) for s in q["expect_source"])
    return result


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def yn(v) -> str:
    return "—" if v is None else ("✓" if v else "✗")


def write_report(provider: str, model: str, rows: list[tuple[dict, dict, dict]], path: str,
                 usage_note: str = "") -> str:
    lines = [
        f"# Agent evaluation — {provider} ({model})",
        "",
        f"Run {datetime.now().strftime('%Y-%m-%d %H:%M')} against the seeded fixture "
        f"(`eval/agent_fixture.py`), {len(rows)} questions, one fresh session each.",
        "",
        "| ID | Probes | Expected tools called | Tools called (in order) | Rounds | % cited | % not in tool results | Hedged | Source named |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for q, rec, c in rows:
        lines.append(
            f"| {q['id']} | {q['probes']} | {yn(c['tools_ok'])} | {', '.join(c['called']) or '—'} | "
            f"{rec['model_rounds']} | {', '.join(f'{p:g}' for p in c['cited_pct']) or '—'} | "
            f"{', '.join(f'{p:g}' for p in c['ungrounded_pct']) or '—'} | {yn(c.get('hedged'))} | {yn(c.get('sourced'))} |"
        )

    n = len(rows)
    tools_ok = sum(c["tools_ok"] for _, _, c in rows)
    multi = sum(len(set(c["called"])) > 1 for _, _, c in rows)
    grounded = sum(not c["ungrounded_pct"] for _, _, c in rows if c["cited_pct"])
    with_pct = sum(bool(c["cited_pct"]) for _, _, c in rows)
    hedge_rows = [c for _, _, c in rows if "hedged" in c]
    source_rows = [c for _, _, c in rows if "sourced" in c]
    lines += [
        "",
        "## Summary",
        "",
        f"- Called every expected tool: {tools_ok}/{n}",
        f"- Used more than one distinct tool: {multi}/{n}",
        f"- Every cited percentage found in tool results: {grounded}/{with_pct} replies that cited percentages",
        f"- Expressed uncertainty where data was low-confidence or inconsistent: "
        f"{sum(c['hedged'] for c in hedge_rows)}/{len(hedge_rows)}",
        f"- Named the expected source: {sum(c['sourced'] for c in source_rows)}/{len(source_rows)}",
        f"- Transient API errors (503/429, retried from a fresh session): "
        f"{sum(len(r.get('failed_attempts', [])) for _, r, _ in rows)} across "
        f"{sum(bool(r.get('failed_attempts')) for _, r, _ in rows)} questions; "
        f"unrecovered: {sum(bool(r.get('error')) for _, r, _ in rows)}",
        f"- Usage: {usage_note}" if usage_note else "",
        "",
        "Percentages not found in tool results may be legitimate arithmetic (e.g. 2 days × 10% = 20%); "
        "check them in the transcripts. The hedge check is a keyword match and should be confirmed by reading.",
        "",
        "## Transcripts",
    ]
    for q, rec, c in rows:
        lines += ["", f"### {q['id']}: {q['question']}", "", f"_Probes: {q['probes']}_", ""]
        for tc in rec["tool_calls"]:
            lines.append(f"- `{tc['name']}({json.dumps(tc['args'])})`")
        if rec.get("error"):
            lines.append(f"\n**Error:** `{rec['error']}`")
        lines += ["", "> " + (rec.get("reply") or "(no reply)").replace("\n", "\n> ")]
    text = "\n".join(lines) + "\n"
    with open(path, "w") as f:
        f.write(text)
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=["gemini", "openai"], required=True)
    ap.add_argument("--model", default=None,
                    help="gemini: overrides GEMINI_MODEL (default gemini-2.5-flash); openai: default gpt-4o")
    ap.add_argument("--only", default=None, help="comma-separated question IDs")
    ap.add_argument("--max-requests", type=int, default=None,
                    help="stop before a question that could exceed this many API requests "
                         "(e.g. 20 for the Gemini free tier's daily limit)")
    ap.add_argument("--max-cost", type=float, default=0.90,
                    help="hard stop in USD, estimated from logged tokens (default 0.90)")
    ap.add_argument("--price-in", type=float, default=2.00,
                    help="assumed USD per 1M input tokens; set >= the published price (default 2.00)")
    ap.add_argument("--price-out", type=float, default=15.00,
                    help="assumed USD per 1M output+thinking tokens; set >= the published price (default 15.00)")
    args = ap.parse_args()

    store = MongoStore()
    if store.db.name != EVAL_DB or not store.courses.count_documents({}):
        sys.exit(f"Seed the test database first: python eval/agent_fixture.py  (using {store.db.name})")

    with open(QUESTIONS) as f:
        questions = json.load(f)
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]

    if args.provider == "gemini":
        if args.model:
            os.environ["GEMINI_MODEL"] = args.model  # read by web_server at import
        model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    else:
        model = args.model or "gpt-4o"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    os.makedirs(OUT_DIR, exist_ok=True)
    log_path = os.path.join(OUT_DIR, f"agent_eval_{args.provider}_{stamp}.jsonl")

    rows = []
    spent, worst = 0.0, 0.10  # assume a question can cost up to $0.10 until measured
    used, most_rounds = 0, 4  # assume a question can take up to 4 requests until measured
    for q in questions:
        if args.max_requests is not None and used + most_rounds > args.max_requests:
            print(f"\nRequest guard: {used} requests used, next question could take ~{most_rounds}, "
                  f"limit {args.max_requests}. Stopping before {q['id']}.")
            break
        if spent + worst > args.max_cost:
            print(f"\nBudget guard: ${spent:.3f} spent, next question could cost ~${worst:.3f}, "
                  f"cap ${args.max_cost:.2f}. Stopping before {q['id']}.")
            break
        print(f"{q['id']}: {q['question']}", flush=True)
        if args.provider == "gemini":
            try:
                rec = run_gemini(q["question"], log_path)
            except DailyQuotaExceeded as e:
                done = ",".join(r[0]["id"] for r in rows)
                print(f"\nDaily API quota exhausted; stopping. Completed: {done or 'none'}.\n{e}")
                break
        else:
            rec = run_openai(q["question"], log_path, store, model)
        prev_used = used
        used = logged_requests(log_path)
        most_rounds = max(most_rounds, used - prev_used)
        before = spent
        spent = logged_cost(log_path, args.price_in, args.price_out)[2]
        worst = max(worst, spent - before)
        c = check(q, rec)
        print(f"    tools={c['called']} ungrounded%={c['ungrounded_pct']} "
              f"hedged={c.get('hedged')} sourced={c.get('sourced')} error={rec.get('error')} "
              f"requests={used} est_spent=${spent:.3f}", flush=True)
        rows.append((q, rec, c))

    report_path = os.path.join(OUT_DIR, f"agent_eval_{args.provider}_{stamp}.md")
    if not rows:
        print("No questions completed; no report written.")
        return
    tin, tout, cost = logged_cost(log_path, args.price_in, args.price_out)
    usage_note = (f"{tin:,} input and {tout:,} output/thinking tokens across all attempts; "
                  f"estimated cost ${cost:.3f} at assumed prices ${args.price_in}/M in, "
                  f"${args.price_out}/M out (actual billing is per Google's published rates)")
    write_report(args.provider, model, rows, report_path, usage_note)
    print(f"\nlog:    {os.path.relpath(log_path, ROOT)}\nreport: {os.path.relpath(report_path, ROOT)}")


if __name__ == "__main__":
    main()

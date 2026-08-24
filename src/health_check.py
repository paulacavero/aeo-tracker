"""
Did today's run actually collect what it should?

The 2026-08 outage wasn't invisible because nothing failed — it was invisible
because the failure was *partial*. Runs kept "succeeding" while collecting 21
of 79 prompts and no Claude data at all. A pass/fail exit code would not have
caught that; coverage thresholds do.

Usage:
  python3 src/health_check.py                     # today; exit 1 if unhealthy
  python3 src/health_check.py --alert             # ...and email if unhealthy
  python3 src/health_check.py --date 2026-08-12   # audit an earlier day

Extra context (e.g. which pipeline steps failed) can be piped in on stdin and
is included in the report.
"""

import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).parent.parent

# Runs as a script (python3 src/health_check.py), so the project root isn't on
# sys.path by default and `from src import alert` would fail.
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

# Fraction of prompts an engine must return usable responses for. Below this,
# the day's numbers aren't comparable to other days and shouldn't be trusted.
MIN_COVERAGE = 0.8


def main():
    alert_mode = "--alert" in sys.argv

    target_date = None
    if "--date" in sys.argv:
        i = sys.argv.index("--date")
        if i + 1 < len(sys.argv):
            target_date = sys.argv[i + 1]

    extra = ""
    if not sys.stdin.isatty():
        try:
            extra = sys.stdin.read().strip()
        except Exception:
            extra = ""

    settings = json.load(open(BASE / "config" / "settings.json"))
    prompts = json.load(open(BASE / "config" / "prompts.json"))
    expected = len(prompts)
    engines = settings["engines"]
    today = target_date or str(date.today())

    db = sqlite3.connect(BASE / "data" / "results.db")

    def count(where, params):
        return db.execute(
            f"SELECT COUNT(*) FROM responses WHERE {where}", params
        ).fetchone()[0]

    lines = [f"AEO tracker health — {today}", ""]
    problems = []

    for engine in engines:
        rows = count("date = ? AND engine = ?", (today, engine))
        usable = count(
            "date = ? AND engine = ? AND response_text != ''", (today, engine)
        )
        cited = count(
            "date = ? AND engine = ? AND urls_cited != '[]'", (today, engine)
        )
        pct = (usable / expected * 100) if expected else 0
        lines.append(
            f"  {engine}: {usable}/{expected} usable responses ({pct:.0f}%), "
            f"{cited} with citations, {rows} rows total"
        )
        if usable < expected * MIN_COVERAGE:
            problems.append(
                f"{engine} collected only {usable}/{expected} usable responses"
            )

    if extra:
        lines += ["", "Pipeline steps:", *(f"  {l}" for l in extra.splitlines() if l.strip())]

    if problems:
        lines += ["", "PROBLEMS:", *(f"  - {p}" for p in problems)]
    else:
        lines += ["", "All engines within coverage threshold."]

    report = "\n".join(lines)
    print(report)

    unhealthy = bool(problems) or bool(extra)
    if unhealthy and alert_mode:
        from src import alert
        subject = f"AEO tracker: run needs attention ({today})"
        alert.send(subject, report + "\n\nLog: ~/aeo-tracker/logs/scan.log\n")

    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

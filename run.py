#!/usr/bin/env python3
"""
AEO Tracker — entry point.

Usage:
  python3 run.py now                        # daily API scan (reliable, scheduled)
  python3 run.py now --date 2026-04-21      # run for a specific date
  python3 run.py browser                    # browser spot-check (real ChatGPT/Claude UI)
  python3 run.py schedule                   # run daily at time in settings.json
  python3 run.py dashboard                  # regenerate dashboard.html only
"""

import sys
import schedule
import time
from pathlib import Path
from src import runner
from src import database
from src import dashboard as dash_module


def run_now(target_date=None):
    runner.run_daily(target_date=target_date, engine_mode="api")
    dash_module.generate()


def run_browser(target_date=None):
    """
    Faithful spot-check against the real ChatGPT/Claude websites. Stored in a
    SEPARATE database + dashboard so it never distorts the daily API trend.
    Requires a logged-in automation Chrome (see setup_auth.py).
    """
    base = Path(__file__).parent
    database.DB_PATH = base / "data" / "results_browser.db"
    runner.run_daily(target_date=target_date, engine_mode="browser")
    dash_module.generate(output_path=base / "dashboard_browser.html")
    print(f"\nBrowser spot-check saved to data/results_browser.db")
    print(f"Dashboard: {base / 'dashboard_browser.html'}")


def run_schedule():
    import json
    from pathlib import Path
    with open(Path(__file__).parent / "config" / "settings.json") as f:
        settings = json.load(f)
    run_time = settings.get("schedule_time", "09:00")
    print(f"Scheduled to run daily at {run_time}. Press Ctrl+C to stop.")
    schedule.every().day.at(run_time).do(run_now)
    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    args = sys.argv[1:]

    if not args or args[0] == "help":
        print(__doc__)
        sys.exit(0)

    command = args[0]

    target_date = None
    if "--date" in args:
        idx = args.index("--date")
        if idx + 1 < len(args):
            target_date = args[idx + 1]

    if command == "now":
        run_now(target_date=target_date)
    elif command == "browser":
        run_browser(target_date=target_date)
    elif command == "schedule":
        run_schedule()
    elif command == "dashboard":
        dash_module.generate()
    else:
        print(f"Unknown command: {command}")
        sys.exit(1)

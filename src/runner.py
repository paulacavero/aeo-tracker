"""
Orchestrates daily runs: iterates all prompts × engines, calls API modules,
stores results in SQLite.
"""

import json
import time
from datetime import date as date_module
from pathlib import Path
from dotenv import load_dotenv

from . import database, detector
from . import api_chatgpt, api_claude

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

CONFIG_DIR = Path(__file__).parent.parent / "config"

API_MODULES = {
    "chatgpt": api_chatgpt,
    "claude":  api_claude,
}

# Seconds between API calls — avoids hammering rate limits
DELAY_BETWEEN_CALLS = 3


def load_config():
    with open(CONFIG_DIR / "settings.json") as f:
        settings = json.load(f)
    with open(CONFIG_DIR / "prompts.json") as f:
        prompts = json.load(f)
    return settings, prompts


def run_daily(target_date=None, skip_existing=True, engine_mode="api"):
    """
    engine_mode:
      "api"     — query the OpenAI/Anthropic APIs (reliable, headless, default).
      "browser" — drive the real ChatGPT/Claude websites via Playwright (faithful
                  to what users see, but flaky). Used for occasional spot-checks;
                  the caller should point database.DB_PATH at a separate file so
                  these results don't mix into the daily trend.
    """
    settings, prompts = load_config()
    today = target_date or str(date_module.today())
    engines = settings["engines"]
    brand = settings["brand"]
    competitors = settings["competitors"]

    if engine_mode == "browser":
        from . import browser_chatgpt, browser_claude
        modules = {"chatgpt": browser_chatgpt, "claude": browser_claude}
    else:
        modules = API_MODULES

    database.init_db()

    total = len(prompts) * len(engines)
    done = skipped = errors = 0

    print(f"\n=== AEO Tracker — {today} (mode: {engine_mode}) ===")
    print(f"Prompts: {len(prompts)} | Engines: {engines} | Total: {total}\n")

    # Browser mode needs a single shared Playwright session for the whole run.
    pw = None
    if engine_mode == "browser":
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()

    try:
        for engine in engines:
            module = modules.get(engine)
            if not module:
                print(f"Unknown engine '{engine}', skipping.")
                continue

            print(f"--- Engine: {engine.upper()} ---")

            for prompt in prompts:
                pid = prompt["id"]
                text = prompt["text"]

                if skip_existing and database.response_exists(today, pid, engine):
                    print(f"  [{pid}] Already done, skipping.")
                    skipped += 1
                    done += 1
                    continue

                print(f"  [{pid}] {text[:70]}...")
                result = module.run_prompt(text) if engine_mode == "api" else module.run_prompt(pw, text)

                if result is None:
                    print(f"  [{pid}] Failed.")
                    errors += 1
                    database.insert_response(
                        today, pid, text, engine,
                        response_text="",
                        latitude_mentioned=False,
                        latitude_cited=False,
                        brands_mentioned=[],
                        urls_cited=[]
                    )
                else:
                    response_text = result["response_text"] or ""
                    urls_cited = result["urls_cited"]

                    brands_found = detector.detect_brands(response_text, brand, competitors)
                    lat_mentioned = detector.detect_latitude_mentioned(brands_found, brand["name"])
                    lat_cited = detector.detect_latitude_cited(urls_cited, brand["domain"])

                    database.insert_response(
                        today, pid, text, engine,
                        response_text=response_text,
                        latitude_mentioned=lat_mentioned,
                        latitude_cited=lat_cited,
                        brands_mentioned=brands_found,
                        urls_cited=urls_cited
                    )

                    status = "MENTIONED" if lat_mentioned else "not mentioned"
                    cited_str = " + CITED" if lat_cited else ""
                    print(f"  [{pid}] {status}{cited_str} | brands: {brands_found}")

                done += 1
                if done < total:
                    time.sleep(DELAY_BETWEEN_CALLS)
    finally:
        if pw is not None:
            pw.stop()

    print(f"\n=== Done — {done} runs, {errors} errors, {skipped} skipped ===")
    summary = database.get_summary()
    print(f"Visibility: {summary['visibility_pct']}% | "
          f"Mentions: {summary['latitude_mentions']} | "
          f"Cited: {summary['latitude_cited']} | "
          f"Days tracked: {summary['days_tracked']}")

"""
Orchestrates daily runs: iterates all prompts × engines, calls API modules,
stores results in SQLite.
"""

import json
import time
from datetime import date as date_module
from pathlib import Path
from dotenv import load_dotenv

from . import database, detector, judge
from . import api_chatgpt, api_claude

load_dotenv(Path(__file__).parent.parent / ".env", override=True)

CONFIG_DIR = Path(__file__).parent.parent / "config"

API_MODULES = {
    "chatgpt": api_chatgpt,
    "claude":  api_claude,
}

# Default query method per engine when settings.json doesn't specify
# `engine_methods`. This is the hybrid: scrape ChatGPT's real site, query Claude
# via the API (with web search). Override per engine in settings.json.
DEFAULT_METHODS = {
    "chatgpt": "browser",
    "claude":  "api",
}

# Seconds between API calls — avoids hammering rate limits
DELAY_BETWEEN_CALLS = 3


def _resolve_methods(engines, settings, override):
    """
    Decide the query method ("api" | "browser") for each engine.
      override=str   -> force every engine to that method (e.g. "browser" spot-check)
      override=dict  -> per-engine map, falling back to defaults
      override=None  -> settings["engine_methods"], then DEFAULT_METHODS
    """
    if isinstance(override, str):
        return {e: override for e in engines}
    cfg = override if isinstance(override, dict) else (settings.get("engine_methods") or {})
    return {e: cfg.get(e, DEFAULT_METHODS.get(e, "api")) for e in engines}


def load_config():
    with open(CONFIG_DIR / "settings.json") as f:
        settings = json.load(f)
    with open(CONFIG_DIR / "prompts.json") as f:
        prompts = json.load(f)
    return settings, prompts


def run_daily(target_date=None, skip_existing=True, methods=None):
    """
    Query each engine via its chosen method ("api" or "browser").

    methods:
      None  -> use settings["engine_methods"], falling back to DEFAULT_METHODS
               (the hybrid: ChatGPT=browser, Claude=api). This is the daily run.
      str   -> force every engine to one method, e.g. "browser" for a spot-check
               (the caller should point database.DB_PATH at a separate file then).
      dict  -> explicit per-engine map.
    """
    settings, prompts = load_config()
    today = target_date or str(date_module.today())
    engines = settings["engines"]
    brand = settings["brand"]
    competitors = settings["competitors"]

    method_for = _resolve_methods(engines, settings, methods)

    # Load browser modules + a shared Playwright session only if some engine
    # actually uses the browser path.
    browser_modules = {}
    if any(m == "browser" for m in method_for.values()):
        from . import browser_chatgpt, browser_claude
        browser_modules = {"chatgpt": browser_chatgpt, "claude": browser_claude}

    database.init_db()

    total = len(prompts) * len(engines)
    done = skipped = errors = 0

    print(f"\n=== AEO Tracker — {today} ===")
    print(f"Prompts: {len(prompts)} | Methods: {method_for} | Total: {total}\n")

    pw = None
    if browser_modules:
        from playwright.sync_api import sync_playwright
        pw = sync_playwright().start()

    try:
        for engine in engines:
            method = method_for[engine]
            module = browser_modules.get(engine) if method == "browser" else API_MODULES.get(engine)
            if not module:
                print(f"No '{method}' module for engine '{engine}', skipping.")
                continue

            print(f"--- Engine: {engine.upper()} (method: {method}) ---")

            for prompt in prompts:
                pid = prompt["id"]
                text = prompt["text"]

                if skip_existing and database.response_exists(today, pid, engine):
                    print(f"  [{pid}] Already done, skipping.")
                    skipped += 1
                    done += 1
                    continue

                print(f"  [{pid}] {text[:70]}...")
                result = module.run_prompt(pw, text) if method == "browser" else module.run_prompt(text)

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

                    # Judge recommended-vs-mentioned only when the brand actually
                    # appears in the text (saves a call when it's absent).
                    lat_recommended = False
                    lat_status = "mentioned" if lat_mentioned else "absent"
                    lat_sentiment = ""
                    lat_rank = None
                    if lat_mentioned:
                        verdict = judge.classify(response_text, brand["name"])
                        if verdict:
                            lat_status = verdict.get("status", lat_status)
                            lat_recommended = (lat_status == "recommended")
                            lat_sentiment = verdict.get("sentiment", "")
                            lat_rank = verdict.get("rank")

                    database.insert_response(
                        today, pid, text, engine,
                        response_text=response_text,
                        latitude_mentioned=lat_mentioned,
                        latitude_cited=lat_cited,
                        brands_mentioned=brands_found,
                        urls_cited=urls_cited,
                        latitude_recommended=lat_recommended,
                        latitude_status=lat_status,
                        latitude_sentiment=lat_sentiment,
                        latitude_rank=lat_rank,
                    )

                    rec_str = " + RECOMMENDED" if lat_recommended else ""
                    cited_str = " + CITED" if lat_cited else ""
                    status = "MENTIONED" if lat_mentioned else "not mentioned"
                    print(f"  [{pid}] {status}{rec_str}{cited_str} | brands: {brands_found}")

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

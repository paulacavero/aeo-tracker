"""
Orchestrates daily runs: iterates all prompts × engines, calls API modules,
stores results in SQLite.
"""

import json
import random
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

# Seconds to wait between calls, per engine. The browser path against
# chatgpt.com needs a far longer gap than an API call: firing prompts a few
# seconds apart gets the session rate-limited, and once OpenAI starts erroring
# the rest of the run is wasted. Override per engine with "delay_between_calls"
# in settings.json.
DEFAULT_DELAYS = {
    "chatgpt": 30,
    "claude":  3,
}
DEFAULT_DELAY = 3

# Vary each wait by ±40% so the gaps aren't a fixed, robotic interval.
DELAY_JITTER = 0.4

# A failure is usually rate limiting, which needs a cooldown — wait this
# multiple of the normal delay before trying the next prompt.
FAILURE_BACKOFF = 3

# Give up on an engine after this many failures in a row. Without this, a dead
# browser session burns hours in per-prompt timeouts instead of moving on.
MAX_CONSECUTIVE_FAILURES = 5


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


def _delay_for(engine, settings):
    """Seconds to wait between calls for this engine."""
    configured = settings.get("delay_between_calls") or {}
    if engine in configured:
        return configured[engine]
    return DEFAULT_DELAYS.get(engine, DEFAULT_DELAY)


def _sleep_between(seconds, failed=False):
    """Wait between prompts, with jitter and a longer pause after a failure."""
    if failed:
        seconds *= FAILURE_BACKOFF
    seconds *= random.uniform(1 - DELAY_JITTER, 1 + DELAY_JITTER)
    time.sleep(seconds)
    return seconds


def _order_engines(engines, method_for):
    """
    Run API engines before browser ones, whatever order settings.json lists
    them in. The browser path is the fragile half: when it breaks it can eat the
    rest of the run, so we want the reliable Claude data already saved by then.
    """
    return sorted(engines, key=lambda e: 0 if method_for.get(e) == "api" else 1)


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
    engines = _order_engines(engines, method_for)

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

            delay = _delay_for(engine, settings)
            consecutive_failures = 0
            print(f"--- Engine: {engine.upper()} (method: {method}, delay: {delay}s) ---")

            for prompt in prompts:
                pid = prompt["id"]
                text = prompt["text"]

                if skip_existing and database.response_exists(today, pid, engine):
                    print(f"  [{pid}] Already done, skipping.")
                    skipped += 1
                    done += 1
                    continue

                print(f"  [{pid}] {text[:70]}...")
                try:
                    result = module.run_prompt(pw, text) if method == "browser" else module.run_prompt(text)
                except Exception as e:
                    # One bad prompt must never kill the run. The browser path
                    # raises from outside its own try/except (Chrome not
                    # starting, CDP connect timing out), which is what took down
                    # every run from 2026-08-07 on.
                    print(f"  [{pid}] Crashed: {type(e).__name__}: {e}")
                    result = None

                if result is None:
                    print(f"  [{pid}] Failed.")
                    errors += 1
                    consecutive_failures += 1
                    database.insert_response(
                        today, pid, text, engine,
                        response_text="",
                        latitude_mentioned=False,
                        latitude_cited=False,
                        brands_mentioned=[],
                        urls_cited=[]
                    )
                else:
                    consecutive_failures = 0
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

                if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                    print(f"\n  {engine}: {consecutive_failures} failures in a row — "
                          f"abandoning this engine, moving on.\n")
                    break

                if done < total:
                    _sleep_between(delay, failed=(result is None))
    finally:
        if pw is not None:
            pw.stop()

    print(f"\n=== Done — {done} runs, {errors} errors, {skipped} skipped ===")
    summary = database.get_summary()
    print(f"Visibility: {summary['visibility_pct']}% | "
          f"Mentions: {summary['latitude_mentions']} | "
          f"Cited: {summary['latitude_cited']} | "
          f"Days tracked: {summary['days_tracked']}")

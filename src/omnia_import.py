"""
One-time migration: pull the full per-prompt history out of Omnia before the
subscription is cancelled, and store it alongside our own data.

Omnia's API has no per-day trends endpoint and ignores granularity params, so
daily granularity means one request per prompt/day/metric/engine. That's ~35k
requests, hence the resumability: every fetched combination is recorded in
omnia_import_log, so an interrupted run picks up where it stopped instead of
starting over.

Only `openai` and `claude` are imported. Omnia also tracks perplexity and
google-ai-overviews, but we don't track those engines ourselves, so importing
them would create a history with no future continuation.

Omnia's numbers are NOT our numbers: it ran its own queries with its own
methodology. This lands in separate omnia_* tables so it can be shown as a
distinct historical series rather than silently spliced into our own trend.

Usage:
  python3 src/omnia_import.py --dry-run     # show the plan, fetch nothing
  python3 src/omnia_import.py               # run (resumable, safe to re-run)
  python3 src/omnia_import.py --metrics visibility,share-of-voice
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

from dotenv import load_dotenv

BASE_DIR = Path(__file__).parent.parent
load_dotenv(BASE_DIR / ".env", override=True)

API = "https://app.useomnia.com/api/v1"
DB_PATH = BASE_DIR / "data" / "results.db"

# Omnia engine slug -> the engine name we use in `responses`
ENGINES = {"openai": "chatgpt", "claude": "claude"}

METRICS = ("visibility", "share-of-voice", "citations", "sentiment")

# Each request round-trips in roughly a second, so serial fetching would take
# ~9 hours for the full history. The API reports X-Ratelimit-Limit: 50 with a
# sub-second reset, so 8 concurrent fetchers stays comfortably under it while
# cutting the job to well under two hours. Fetching is threaded; every SQLite
# write stays on the main thread.
WORKERS = 8
BATCH = 240
MAX_RETRIES = 4


def api_get(path, params, key):
    url = f"{API}{path}?{urlencode(params)}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Accept": "application/json",
    })
    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 2 ** attempt
                print(f"    429, backing off {wait}s")
                time.sleep(wait)
                continue
            if e.code in (400, 404):
                return None          # no data for this combination
            if attempt == MAX_RETRIES - 1:
                print(f"    HTTP {e.code} giving up on {path}")
                return None
            time.sleep(2 ** attempt)
        except Exception as e:
            if attempt == MAX_RETRIES - 1:
                print(f"    {type(e).__name__} giving up on {path}")
                return None
            time.sleep(2 ** attempt)
    return None


def init_tables(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS omnia_visibility (
            date TEXT, prompt_id TEXT, prompt_query TEXT, engine TEXT,
            brand TEXT, domain TEXT, rank INTEGER, visibility REAL,
            relationship TEXT
        );
        CREATE TABLE IF NOT EXISTS omnia_share_of_voice (
            date TEXT, prompt_id TEXT, prompt_query TEXT, engine TEXT,
            brand TEXT, domain TEXT, mention_count INTEGER, rank INTEGER,
            share_of_voice REAL, relationship TEXT
        );
        CREATE TABLE IF NOT EXISTS omnia_citations (
            date TEXT, prompt_id TEXT, prompt_query TEXT, engine TEXT,
            domain TEXT, url TEXT, title TEXT, total_citations INTEGER,
            share_of_voice REAL, type TEXT
        );
        CREATE TABLE IF NOT EXISTS omnia_sentiment (
            date TEXT, prompt_id TEXT, prompt_query TEXT, engine TEXT,
            brand TEXT, domain TEXT, relationship TEXT, rank INTEGER,
            feature_name TEXT, feature_description TEXT,
            endorsed_mentions INTEGER, undermined_mentions INTEGER,
            neutral_mentions INTEGER, total_mentions INTEGER
        );
        -- One row per fetched combination, so an interrupted run resumes.
        CREATE TABLE IF NOT EXISTS omnia_import_log (
            prompt_id TEXT, date TEXT, metric TEXT, engine TEXT,
            rows INTEGER, fetched_at TEXT,
            PRIMARY KEY (prompt_id, date, metric, engine)
        );
        CREATE INDEX IF NOT EXISTS idx_omnia_vis_date  ON omnia_visibility(date);
        CREATE INDEX IF NOT EXISTS idx_omnia_sov_date  ON omnia_share_of_voice(date);
        CREATE INDEX IF NOT EXISTS idx_omnia_cit_date  ON omnia_citations(date);
        CREATE INDEX IF NOT EXISTS idx_omnia_sen_date  ON omnia_sentiment(date);
    """)
    conn.commit()


def store(conn, metric, day, pid, query, engine, rows):
    """Insert one metric's rows for one prompt/day/engine."""
    if metric == "visibility":
        conn.executemany(
            "INSERT INTO omnia_visibility VALUES (?,?,?,?,?,?,?,?,?)",
            [(day, pid, query, engine, r.get("brand"), r.get("domain"),
              r.get("rank"), r.get("visibility"), r.get("relationship"))
             for r in rows])
    elif metric == "share-of-voice":
        conn.executemany(
            "INSERT INTO omnia_share_of_voice VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(day, pid, query, engine, r.get("brand"), r.get("domain"),
              r.get("mentionCount"), r.get("rank"), r.get("shareOfVoice"),
              r.get("relationship")) for r in rows])
    elif metric == "citations":
        conn.executemany(
            "INSERT INTO omnia_citations VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(day, pid, query, engine, r.get("domain"), r.get("url"),
              r.get("title"), r.get("totalCitations"), r.get("shareOfVoice"),
              r.get("type")) for r in rows])
    elif metric == "sentiment":
        conn.executemany(
            "INSERT INTO omnia_sentiment VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [(day, pid, query, engine, r.get("brand"), r.get("domain"),
              r.get("relationship"), r.get("rank"), r.get("featureName"),
              r.get("featureDescription"), r.get("endorsedMentions"),
              r.get("underminedMentions"), r.get("neutralMentions"),
              r.get("totalMentions")) for r in rows])


def fetch_prompts(key):
    brands = api_get("/brands", {"pageSize": "100"}, key)
    if not brands:
        sys.exit("could not list brands — check OMNIA_API_KEY")
    brand = brands["data"]["brands"][0]
    print(f"brand: {brand['name']} ({brand['domain']})")

    out, path, params = [], f"/brands/{brand['id']}/prompts", {"pageSize": "100"}
    while path:
        r = api_get(path, params, key) if path.startswith("/") else None
        if not r:
            break
        out += r["data"]["prompts"]
        nxt = (r.get("links") or {}).get("next")
        path, params = (nxt, {}) if nxt else (None, {})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--metrics", default=",".join(METRICS))
    ap.add_argument("--start", default=None, help="YYYY-MM-DD floor")
    args = ap.parse_args()

    key = os.environ.get("OMNIA_API_KEY")
    if not key:
        sys.exit("OMNIA_API_KEY not set in .env")

    metrics = [m.strip() for m in args.metrics.split(",") if m.strip()]
    for m in metrics:
        if m not in METRICS:
            sys.exit(f"unknown metric {m!r}; choose from {METRICS}")

    prompts = fetch_prompts(key)
    print(f"prompts: {len(prompts)}")

    today = date.today()
    floor = datetime.strptime(args.start, "%Y-%m-%d").date() if args.start else None

    # Each prompt only has history from its own createdAt onward — skipping
    # earlier days avoids thousands of guaranteed-empty requests.
    plan = []
    for p in prompts:
        # Omnia returns e.g. 2026-03-05T10:39:24.6+00:00 — a 1-digit fraction
        # that Python 3.9's fromisoformat rejects. Only the date matters here.
        created = datetime.strptime(p["createdAt"][:10], "%Y-%m-%d").date()
        start = max(created, floor) if floor else created
        days = (today - start).days + 1
        if days > 0:
            plan.append((p, start, days))

    total = sum(d for _, _, d in plan) * len(metrics) * len(ENGINES)
    print(f"metrics: {metrics}")
    print(f"engines: {list(ENGINES)}  (perplexity and google-ai-overviews skipped)")
    print(f"date range: earliest {min(s for _, s, _ in plan)} -> {today}")
    print(f"total requests if nothing is cached: {total:,}")
    # ~1 request/sec per worker measured against this API
    print(f"estimated wall clock with {WORKERS} workers: "
          f"{total / WORKERS / 60:.0f} min")

    if args.dry_run:
        print("\n--dry-run: stopping before any data fetch")
        return

    conn = sqlite3.connect(DB_PATH)
    init_tables(conn)
    done = {r for r in conn.execute(
        "SELECT prompt_id, date, metric, engine FROM omnia_import_log")}
    print(f"already imported: {len(done):,} combinations\n")

    # Build the full work list first, minus whatever a previous run finished.
    tasks = []
    skipped = 0
    for p, start, days in plan:
        pid, query = p["id"], p.get("query", "")
        for i in range(days):
            day = str(start + timedelta(days=i))
            for metric in metrics:
                for slug, engine in ENGINES.items():
                    if (pid, day, metric, slug) in done:
                        skipped += 1
                        continue
                    tasks.append((pid, query, day, metric, slug, engine))

    print(f"to fetch: {len(tasks):,}  (already had {skipped:,})")
    if not tasks:
        print("nothing left to do")
        conn.close()
        return

    def fetch(task):
        pid, query, day, metric, slug, engine = task
        r = api_get(f"/prompts/{pid}/{metric}/aggregates",
                    {"startDate": day, "endDate": day,
                     "pageSize": "100", "engine": slug}, key)
        rows = (r or {}).get("data", {}).get("aggregates", []) or []
        return task, rows

    fetched = empty = 0
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for i in range(0, len(tasks), BATCH):
            chunk = tasks[i:i + BATCH]
            for task, rows in pool.map(fetch, chunk):
                pid, query, day, metric, slug, engine = task
                if rows:
                    store(conn, metric, day, pid, query, engine, rows)
                else:
                    empty += 1
                conn.execute(
                    "INSERT OR REPLACE INTO omnia_import_log VALUES (?,?,?,?,?,?)",
                    (pid, day, metric, slug, len(rows),
                     datetime.now().isoformat(timespec="seconds")))
                fetched += 1
            conn.commit()
            rate = fetched / max(time.time() - t0, 1)
            left = (len(tasks) - fetched) / max(rate, 0.1) / 60
            pct = fetched / len(tasks) * 100
            print(f"  {fetched:,}/{len(tasks):,} ({pct:.0f}%)  "
                  f"{rate:.1f}/s  ~{left:.0f} min left")

    conn.commit()
    print(f"\ndone: {fetched:,} fetched ({empty:,} empty), {skipped:,} already had")
    for t in ("omnia_visibility", "omnia_share_of_voice",
              "omnia_citations", "omnia_sentiment"):
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t}: {n:,} rows")
    conn.close()


if __name__ == "__main__":
    main()

"""
Did the Omnia migration actually capture everything?

Run this BEFORE cancelling the subscription. It re-queries Omnia for the
authoritative prompt list and date span, then checks the local tables against
it. Exits non-zero if anything is missing, so "it looked fine" isn't the basis
for losing access to the source.

Usage:
  python3 src/omnia_verify.py
  python3 src/omnia_verify.py --spot-check 5    # re-fetch N random combinations
                                                 and compare against stored rows
"""

import argparse
import json
import os
import random
import sqlite3
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.omnia_import import (ENGINES, METRICS, api_get,  # noqa: E402
                              fetch_prompts)

BASE_DIR = Path(__file__).parent.parent
DB_PATH = BASE_DIR / "data" / "results.db"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spot-check", type=int, default=3)
    args = ap.parse_args()

    key = os.environ.get("OMNIA_API_KEY")
    if not key:
        sys.exit("OMNIA_API_KEY not set — cannot verify against the source")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    print("=== source of truth: Omnia ===")
    prompts = fetch_prompts(key)
    print(f"prompts in Omnia: {len(prompts)}")

    today = date.today()
    expected = 0
    per_prompt = {}
    for p in prompts:
        created = datetime.strptime(p["createdAt"][:10], "%Y-%m-%d").date()
        days = (today - created).days + 1
        per_prompt[p["id"]] = (p.get("query", ""), created, days)
        expected += days * len(METRICS) * len(ENGINES)

    logged = conn.execute("SELECT COUNT(*) FROM omnia_import_log").fetchone()[0]
    print(f"\n=== local coverage ===")
    print(f"expected combinations: {expected:,}")
    print(f"logged combinations:   {logged:,}")

    problems = []
    if logged < expected:
        problems.append(f"{expected - logged:,} combinations never fetched")

    # per-prompt completeness
    have = defaultdict(int)
    for r in conn.execute(
            "SELECT prompt_id, COUNT(*) c FROM omnia_import_log GROUP BY prompt_id"):
        have[r["prompt_id"]] = r["c"]

    print(f"\n{'prompt':52} {'want':>6} {'have':>6}")
    for pid, (query, created, days) in sorted(
            per_prompt.items(), key=lambda kv: kv[1][0]):
        want = days * len(METRICS) * len(ENGINES)
        got = have.get(pid, 0)
        flag = "" if got >= want else "  <-- INCOMPLETE"
        print(f"{query[:52]:52} {want:6} {got:6}{flag}")
        if got < want:
            problems.append(f"prompt '{query[:40]}' has {got}/{want}")

    # missing entirely?
    missing_prompts = [pid for pid in per_prompt if pid not in have]
    if missing_prompts:
        problems.append(f"{len(missing_prompts)} prompts have no rows at all")

    print(f"\n=== stored rows ===")
    for t in ("omnia_visibility", "omnia_share_of_voice",
              "omnia_citations", "omnia_sentiment"):
        n = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        span = conn.execute(f"SELECT MIN(date), MAX(date) FROM {t}").fetchone()
        print(f"  {t:22} {n:>8,} rows   {span[0]} -> {span[1]}")

    # ---- spot check: re-fetch a few combinations and compare ---- #
    if args.spot_check:
        print(f"\n=== spot check: re-fetching {args.spot_check} combinations ===")
        rows = conn.execute("""
            SELECT prompt_id, date, metric, engine, rows FROM omnia_import_log
            WHERE rows > 0 ORDER BY RANDOM() LIMIT ?
        """, (args.spot_check,)).fetchall()
        for r in rows:
            live = api_get(f"/prompts/{r['prompt_id']}/{r['metric']}/aggregates",
                           {"startDate": r["date"], "endDate": r["date"],
                            "pageSize": "100", "engine": r["engine"]}, key)
            n_live = len((live or {}).get("data", {}).get("aggregates", []) or [])
            ok = "match" if n_live == r["rows"] else f"MISMATCH (live {n_live})"
            print(f"  {r['date']} {r['metric']:15} {r['engine']:8} "
                  f"stored {r['rows']:3}  {ok}")
            if n_live != r["rows"]:
                problems.append(
                    f"spot check mismatch {r['date']} {r['metric']} {r['engine']}")

    conn.close()
    print()
    if problems:
        print("NOT SAFE TO CANCEL — unresolved issues:")
        for p in problems[:20]:
            print(f"  - {p}")
        if len(problems) > 20:
            print(f"  ... and {len(problems)-20} more")
        print("\nRe-run: python3 src/omnia_import.py   (resumes where it stopped)")
        return 1

    print("All checks passed. The local copy matches Omnia's prompt list,")
    print("date spans and row counts. Safe to cancel the subscription.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

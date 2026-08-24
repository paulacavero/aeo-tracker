"""
Exports agent-friendly JSON from results.db into the private aeo-data repo.
Run after the daily scan: python3 src/export_data.py
"""

import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

TRACKER_DIR = Path(__file__).parent.parent
EXPORT_DIR = Path("/Users/paula/aeo-data")
DB_PATH = TRACKER_DIR / "data" / "results.db"


def main():
    settings = json.load(open(TRACKER_DIR / "config" / "settings.json"))
    own_domain = settings["brand"]["domain"]
    competitor_domains = {c["domain"] for c in settings["competitors"]}

    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    rows = db.execute(
        "SELECT date, prompt_id, prompt_text, engine, brands_mentioned, urls_cited "
        "FROM responses"
    ).fetchall()

    # ---- citations.json: one entry per unique cited URL ---- #
    articles = {}
    for r in rows:
        for u in json.loads(r["urls_cited"]):
            a = articles.setdefault(u["url"], {
                "url": u["url"],
                "domain": u["domain"],
                "title": "",
                "times_cited": 0,
                "engines": set(),
                "first_seen": r["date"],
                "last_seen": r["date"],
                "prompts": set(),
            })
            a["times_cited"] += 1
            a["engines"].add(r["engine"])
            a["first_seen"] = min(a["first_seen"], r["date"])
            a["last_seen"] = max(a["last_seen"], r["date"])
            a["prompts"].add(f'{r["prompt_id"]}: {r["prompt_text"]}')
            if len(u.get("title", "")) > len(a["title"]):
                a["title"] = u["title"]

    citations = []
    for a in sorted(articles.values(), key=lambda x: -x["times_cited"]):
        a["engines"] = sorted(a["engines"])
        a["prompts"] = sorted(a["prompts"])
        a["is_own_domain"] = own_domain in a["domain"]
        a["is_competitor_domain"] = any(d in a["domain"] for d in competitor_domains)
        citations.append(a)

    # ---- brands.json: mentions per brand per day ---- #
    by_day = defaultdict(lambda: defaultdict(int))
    for r in rows:
        for brand in json.loads(r["brands_mentioned"]):
            by_day[r["date"]][brand] += 1

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_responses": len(rows),
        "date_range": [min(r["date"] for r in rows), max(r["date"] for r in rows)],
        "note": "Citations come from Claude responses only (ChatGPT free tier does not trigger web search).",
    }

    EXPORT_DIR.mkdir(exist_ok=True)
    json.dump({"meta": meta, "articles": citations},
              open(EXPORT_DIR / "citations.json", "w"), indent=1)
    json.dump({"meta": meta, "mentions_by_day": by_day},
              open(EXPORT_DIR / "brands.json", "w"), indent=1)

    print(f"export: {len(citations)} articles, {len(by_day)} days -> {EXPORT_DIR}")


if __name__ == "__main__":
    main()

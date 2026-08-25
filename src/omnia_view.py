"""
Turns the imported Omnia tables into a compact series the dashboard can render.

This is history from a tool we no longer pay for, kept as its own labelled
series. It is NOT spliced into our own numbers: Omnia ran its own queries with
its own methodology, so the two are adjacent evidence, not one continuous line.

Only openai/claude were imported (see omnia_import), which is why this lines up
with the engines we track rather than covering Omnia's full engine set.
"""

import sqlite3
from collections import defaultdict
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "results.db"

# Cap the number of brands sent to the browser. Omnia auto-detects 200+ brands;
# most appear once and would bloat the HTML for no visual gain.
TOP_BRANDS = 14


def _has_tables(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return "omnia_share_of_voice" in names


def load(db_path=None):
    """Return the Omnia history block, or None if nothing was imported."""
    path = db_path or DB_PATH
    if not Path(path).exists():
        return None

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    if not _has_tables(conn):
        conn.close()
        return None

    rows = conn.execute("""
        SELECT date, engine, brand, relationship,
               SUM(COALESCE(mention_count, 0)) AS mentions
        FROM omnia_share_of_voice
        GROUP BY date, engine, brand, relationship
    """).fetchall()
    if not rows:
        conn.close()
        return None

    # Share of voice has to be recomputed from mention counts, not averaged
    # across prompts: a prompt with 2 mentions and one with 40 are not equal
    # weights, and averaging their percentages would misreport both.
    totals = defaultdict(int)                      # (date, engine) -> mentions
    per_brand = defaultdict(int)                   # (date, engine, brand)
    brand_total = defaultdict(int)                 # brand -> mentions
    owned = set()
    for r in rows:
        k = (r["date"], r["engine"])
        totals[k] += r["mentions"]
        per_brand[(r["date"], r["engine"], r["brand"])] += r["mentions"]
        brand_total[r["brand"]] += r["mentions"]
        if r["relationship"] == "owned":
            owned.add(r["brand"])

    top = sorted(brand_total, key=lambda b: -brand_total[b])[:TOP_BRANDS]
    keep = list(dict.fromkeys(list(owned) + top))   # own brand always included

    days = sorted({r["date"] for r in rows})
    engines = sorted({r["engine"] for r in rows})

    sov = {e: {} for e in engines}
    for d in days:
        for e in engines:
            tot = totals.get((d, e), 0)
            if not tot:
                continue
            day_row = {}
            for b in keep:
                m = per_brand.get((d, e, b), 0)
                if m:
                    day_row[b] = round(m / tot * 100, 2)
            if day_row:
                sov[e][d] = day_row

    # Citations aggregated over the whole imported window
    cites = conn.execute("""
        SELECT url, domain, title, type,
               SUM(COALESCE(total_citations, 0)) AS total
        FROM omnia_citations
        GROUP BY url
        HAVING total > 0
        ORDER BY total DESC
        LIMIT 300
    """).fetchall()

    # Feature-level sentiment — the one metric we don't compute ourselves
    feats = conn.execute("""
        SELECT brand, feature_name,
               SUM(COALESCE(endorsed_mentions,0))   AS endorsed,
               SUM(COALESCE(undermined_mentions,0)) AS undermined,
               SUM(COALESCE(total_mentions,0))      AS total
        FROM omnia_sentiment
        WHERE feature_name IS NOT NULL AND feature_name != ''
        GROUP BY brand, feature_name
        HAVING total > 0
        ORDER BY total DESC
        LIMIT 200
    """).fetchall()

    prompt_count = conn.execute(
        "SELECT COUNT(DISTINCT prompt_id) FROM omnia_share_of_voice"
    ).fetchone()[0]
    conn.close()

    return {
        "days": days,
        "engines": engines,
        "brands": keep,
        "owned": sorted(owned),
        "sovByDay": sov,
        "promptCount": prompt_count,
        "dateRange": [days[0], days[-1]],
        "citations": [dict(r) for r in cites],
        "features": [dict(r) for r in feats],
    }


if __name__ == "__main__":
    import json
    d = load()
    if not d:
        print("no Omnia data imported yet")
    else:
        print(f"days:     {len(d['days'])}  ({d['dateRange'][0]} -> {d['dateRange'][1]})")
        print(f"engines:  {d['engines']}")
        print(f"prompts:  {d['promptCount']}")
        print(f"brands:   {len(d['brands'])} kept, owned={d['owned']}")
        print(f"citations:{len(d['citations'])}  features:{len(d['features'])}")
        for e in d["engines"]:
            last = sorted(d["sovByDay"][e])[-1] if d["sovByDay"][e] else None
            if last:
                row = d["sovByDay"][e][last]
                top3 = sorted(row.items(), key=lambda x: -x[1])[:3]
                print(f"  {e} {last}: " + ", ".join(f"{b} {v}%" for b, v in top3))
        print(f"\napprox JSON size: {len(json.dumps(d))/1024:.0f} KB")

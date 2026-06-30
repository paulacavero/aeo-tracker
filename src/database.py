import sqlite3
import json
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "results.db"


def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS responses (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                date            TEXT NOT NULL,
                prompt_id       TEXT NOT NULL,
                prompt_text     TEXT NOT NULL,
                engine          TEXT NOT NULL,
                response_text   TEXT,
                latitude_mentioned  INTEGER NOT NULL DEFAULT 0,
                latitude_cited      INTEGER NOT NULL DEFAULT 0,
                brands_mentioned    TEXT NOT NULL DEFAULT '[]',
                urls_cited          TEXT NOT NULL DEFAULT '[]',
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_date_engine
            ON responses (date, engine)
        """)
        conn.commit()
    print("Database ready.")


def insert_response(date, prompt_id, prompt_text, engine, response_text,
                    latitude_mentioned, latitude_cited, brands_mentioned, urls_cited):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO responses
                (date, prompt_id, prompt_text, engine, response_text,
                 latitude_mentioned, latitude_cited, brands_mentioned, urls_cited)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date, prompt_id, prompt_text, engine, response_text,
            int(latitude_mentioned), int(latitude_cited),
            json.dumps(brands_mentioned), json.dumps(urls_cited)
        ))
        conn.commit()


def response_exists(date, prompt_id, engine):
    with sqlite3.connect(DB_PATH) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM responses WHERE date=? AND prompt_id=? AND engine=?",
            (date, prompt_id, engine)
        ).fetchone()[0]
    return count > 0


def get_all_responses():
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM responses ORDER BY date ASC, prompt_id ASC, engine ASC"
        ).fetchall()
    result = []
    for r in rows:
        row = dict(r)
        row["brands_mentioned"] = json.loads(row["brands_mentioned"])
        row["urls_cited"] = json.loads(row["urls_cited"])
        result.append(row)
    return result


def get_summary():
    """Returns a quick summary dict for CLI display."""
    with sqlite3.connect(DB_PATH) as conn:
        total = conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
        mentions = conn.execute(
            "SELECT COUNT(*) FROM responses WHERE latitude_mentioned=1"
        ).fetchone()[0]
        cited = conn.execute(
            "SELECT COUNT(*) FROM responses WHERE latitude_cited=1"
        ).fetchone()[0]
        days = conn.execute(
            "SELECT COUNT(DISTINCT date) FROM responses"
        ).fetchone()[0]
    return {
        "total_responses": total,
        "latitude_mentions": mentions,
        "latitude_cited": cited,
        "days_tracked": days,
        "visibility_pct": round(mentions / total * 100, 2) if total else 0,
    }

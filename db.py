import sqlite3
import json
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).parent / "scanner_history.db"


def _get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            total_tokens_found INTEGER NOT NULL,
            top_score INTEGER NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scan_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_id INTEGER NOT NULL,
            token_address TEXT NOT NULL,
            token_name TEXT,
            symbol TEXT,
            score INTEGER,
            price_usd REAL,
            liquidity_usd REAL,
            volume_24h REAL,
            fdv REAL,
            pair_age_hours REAL,
            signals TEXT,
            warnings TEXT,
            FOREIGN KEY (scan_id) REFERENCES scans(id)
        )
    """)
    conn.commit()
    return conn


def save_scan(results):
    if not results:
        return None
    conn = _get_conn()
    try:
        ts = datetime.now(timezone.utc).isoformat()
        top_score = max(r.get("score", 0) for r in results)
        cur = conn.execute(
            "INSERT INTO scans (timestamp, total_tokens_found, top_score) VALUES (?, ?, ?)",
            (ts, len(results), top_score),
        )
        scan_id = cur.lastrowid
        for r in results:
            conn.execute(
                """INSERT INTO scan_results
                   (scan_id, token_address, token_name, symbol, score, price_usd,
                    liquidity_usd, volume_24h, fdv, pair_age_hours, signals, warnings)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    scan_id,
                    r.get("token_address", ""),
                    r.get("token_name", ""),
                    r.get("token_symbol", ""),
                    r.get("score", 0),
                    r.get("price_usd", 0),
                    r.get("liquidity", 0),
                    r.get("volume_24h", 0),
                    r.get("fdv", 0),
                    r.get("pair_age_hours", 0),
                    r.get("signal", ""),
                    json.dumps(r.get("warnings", []), ensure_ascii=False),
                ),
            )
        conn.commit()
        return scan_id
    finally:
        conn.close()


def get_latest_scan():
    conn = _get_conn()
    try:
        scan = conn.execute(
            "SELECT * FROM scans ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if not scan:
            return None
        scan = dict(scan)
        rows = conn.execute(
            "SELECT * FROM scan_results WHERE scan_id = ? ORDER BY score DESC LIMIT 10",
            (scan["id"],),
        ).fetchall()
        scan["results"] = [dict(r) for r in rows]
        return scan
    finally:
        conn.close()


def get_scan_history(limit=50):
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM scans ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_scan_details(scan_id):
    conn = _get_conn()
    try:
        rows = conn.execute(
            "SELECT * FROM scan_results WHERE scan_id = ? ORDER BY score DESC", (scan_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()

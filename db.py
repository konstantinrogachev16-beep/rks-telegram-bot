import sqlite3
from typing import Any, Dict, List
from datetime import datetime

DB_PATH = "rks.db"

def init_db() -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        tg_user_id INTEGER NOT NULL,
        tg_username TEXT,
        name TEXT,
        phone TEXT,
        car TEXT,
        segment_trigger TEXT,
        pain_main TEXT,
        services_interest TEXT,
        ready_time TEXT,
        lead_temp TEXT,
        contact_method TEXT,
        comment_free TEXT,
        source TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS managers (
        tg_user_id INTEGER PRIMARY KEY,
        tg_username TEXT,
        name TEXT,
        added_at TEXT NOT NULL
    )
    """)

    conn.commit()
    conn.close()

def save_lead(data: Dict[str, Any]) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    INSERT INTO leads (
        created_at, tg_user_id, tg_username, name, phone, car,
        segment_trigger, pain_main, services_interest, ready_time,
        lead_temp, contact_method, comment_free, source
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("created_at") or datetime.utcnow().isoformat(),
        data["tg_user_id"],
        data.get("tg_username"),
        data.get("name"),
        data.get("phone"),
        data.get("car"),
        data.get("segment_trigger"),
        data.get("pain_main"),
        data.get("services_interest"),
        data.get("ready_time"),
        data.get("lead_temp"),
        data.get("contact_method"),
        data.get("comment_free"),
        data.get("source"),
    ))
    conn.commit()
    lead_id = cur.lastrowid
    conn.close()
    return lead_id

def add_manager(tg_user_id: int, tg_username: str | None, name: str | None) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
    INSERT OR REPLACE INTO managers (tg_user_id, tg_username, name, added_at)
    VALUES (?, ?, ?, ?)
    """, (tg_user_id, tg_username, name, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()

def remove_manager(tg_user_id: int) -> None:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("DELETE FROM managers WHERE tg_user_id = ?", (tg_user_id,))
    conn.commit()
    conn.close()

def list_managers() -> List[dict]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT tg_user_id, tg_username, name, added_at FROM managers ORDER BY added_at DESC")
    rows = cur.fetchall()
    conn.close()

    out: List[dict] = []
    for r in rows:
        out.append({
            "tg_user_id": int(r[0]),
            "tg_username": r[1],
            "name": r[2],
            "added_at": r[3],
        })
    return out

def list_manager_ids() -> List[int]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT tg_user_id FROM managers")
    rows = cur.fetchall()
    conn.close()
    return [int(r[0]) for r in rows]
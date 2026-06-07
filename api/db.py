"""SQLite state management for mediaflow API."""
import aiosqlite
import os
from pathlib import Path

DB_PATH = Path(os.getenv("DB_PATH", "./pipeline.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    stem            TEXT PRIMARY KEY,
    filename        TEXT,
    status          TEXT NOT NULL DEFAULT 'submitted',
    current_stage   TEXT,
    submitted_at    REAL,
    started_at      REAL,
    completed_at    REAL,
    duration_sec    REAL,
    error_msg       TEXT,
    output_srt_path TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    stem    TEXT    NOT NULL,
    event   TEXT    NOT NULL,
    stage   TEXT,
    status  TEXT,
    ts      REAL,
    payload TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_stem ON events(stem);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
"""


async def init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def upsert_task(stem: str, **kwargs):
    cols = ", ".join(kwargs.keys())
    placeholders = ", ".join(f":{k}" for k in kwargs)
    updates = ", ".join(f"{k} = excluded.{k}" for k in kwargs)
    sql = f"""
        INSERT INTO tasks (stem, {cols})
        VALUES (:stem, {placeholders})
        ON CONFLICT(stem) DO UPDATE SET {updates}
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(sql, {"stem": stem, **kwargs})
        await db.commit()


async def insert_event(stem: str, event: str, **kwargs):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO events (stem, event, stage, status, ts, payload) VALUES (?,?,?,?,?,?)",
            (stem, event, kwargs.get("stage"), kwargs.get("status"),
             kwargs.get("ts"), kwargs.get("payload")),
        )
        await db.commit()


async def get_status_overview() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute(
            "SELECT * FROM tasks WHERE status = 'processing' ORDER BY started_at DESC"
        )
        processing = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM tasks WHERE status = 'submitted' ORDER BY submitted_at ASC"
        )
        queue = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM tasks WHERE status = 'completed' ORDER BY completed_at DESC LIMIT 20"
        )
        recent = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            "SELECT * FROM tasks WHERE status = 'failed' ORDER BY completed_at DESC LIMIT 10"
        )
        failed = [dict(r) for r in await cur.fetchall()]

    return {"processing": processing, "queue": queue, "recent": recent, "failed": failed}

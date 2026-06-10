"""SQLite state management for mediaflow API."""
import aiosqlite
import os
import time
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

CREATE TABLE IF NOT EXISTS reruns (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    stem          TEXT NOT NULL,
    from_stage    TEXT,
    requested_at  REAL NOT NULL
);
"""


_MIGRATIONS = [
    "ALTER TABLE tasks ADD COLUMN minio_input_key TEXT",
    "ALTER TABLE tasks ADD COLUMN minio_output_prefix TEXT",
]


async def init():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        for sql in _MIGRATIONS:
            try:
                await db.execute(sql)
            except aiosqlite.OperationalError:
                pass  # column already exists
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


async def get_task_aggregates() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*), COALESCE(SUM(duration_sec), 0), "
            "SUM(CASE WHEN status='completed' THEN 1 ELSE 0 END) "
            "FROM tasks"
        )
        row = await cur.fetchone()
        return {
            "total_tasks": row[0] or 0,
            "total_duration_sec": float(row[1] or 0),
            "completed": row[2] or 0,
        }


async def get_task(stem: str) -> "dict | None":
    """Return task row as dict, or None if not found."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tasks WHERE stem = ?", (stem,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def count_active_tasks() -> int:
    """Count tasks occupying a pipeline slot."""
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM tasks "
            "WHERE status IN ('downloading','queued','submitted','processing')"
        )
        row = await cur.fetchone()
        return row[0] if row else 0


async def get_oldest_pending() -> "dict | None":
    """Return the oldest pending upload task, or None if queue is empty."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM tasks WHERE status = 'pending' "
            "ORDER BY submitted_at ASC LIMIT 1"
        )
        row = await cur.fetchone()
        return dict(row) if row else None


async def get_upload_queue() -> list:
    """All upload-originated tasks (minio_input_key IS NOT NULL), newest first."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM tasks WHERE minio_input_key IS NOT NULL "
            "ORDER BY submitted_at DESC"
        )
        return [dict(r) for r in await cur.fetchall()]


async def delete_task(stem: str) -> None:
    """Permanently delete a task row (used to cancel pending uploads)."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM tasks WHERE stem = ?", (stem,))
        await db.commit()


async def get_stage_events(stem: str) -> list:
    """Return stage.completed events for stem ordered by ts ascending."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT stage, ts FROM events "
            "WHERE stem = ? AND event = 'stage.completed' AND stage IS NOT NULL AND stage != '' "
            "ORDER BY ts ASC",
            (stem,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def insert_rerun(stem: str, from_stage: "str | None") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO reruns (stem, from_stage, requested_at) VALUES (?, ?, ?)",
            (stem, from_stage, time.time()),
        )
        await db.commit()


async def pop_oldest_rerun() -> "dict | None":
    """Pop and return the oldest rerun request, or None if the queue is empty."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM reruns ORDER BY requested_at ASC LIMIT 1"
        )
        row = await cur.fetchone()
        if row:
            await db.execute("DELETE FROM reruns WHERE id = ?", (row["id"],))
            await db.commit()
        return dict(row) if row else None

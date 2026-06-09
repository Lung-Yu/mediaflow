"""Tests for task management API — submission, rerun, delete."""
import asyncio
import os
import pytest

os.environ.setdefault("DB_PATH", ":memory:")


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    import importlib
    import api.db as db_mod
    importlib.reload(db_mod)
    asyncio.get_event_loop().run_until_complete(db_mod.init())
    return db_mod


def test_reruns_table_exists(tmp_db):
    """Schema migration creates reruns table with expected columns."""
    import aiosqlite

    async def check():
        async with aiosqlite.connect(tmp_db.DB_PATH) as db:
            cur = await db.execute("PRAGMA table_info(reruns)")
            cols = {row[1] for row in await cur.fetchall()}
        assert "stem" in cols
        assert "from_stage" in cols
        assert "requested_at" in cols

    asyncio.get_event_loop().run_until_complete(check())


def test_insert_and_pop_rerun(tmp_db):
    """insert_rerun persists a row; pop_oldest_rerun returns and removes it."""
    async def run():
        await tmp_db.insert_rerun("s1", "summarize")
        row = await tmp_db.pop_oldest_rerun()
        assert row is not None
        assert row["stem"] == "s1"
        assert row["from_stage"] == "summarize"
        empty = await tmp_db.pop_oldest_rerun()
        assert empty is None

    asyncio.get_event_loop().run_until_complete(run())


def test_pop_returns_fifo_order(tmp_db):
    """pop_oldest_rerun returns the earliest-inserted row first."""
    async def run():
        await tmp_db.insert_rerun("first", None)
        await tmp_db.insert_rerun("second", "summarize")
        row = await tmp_db.pop_oldest_rerun()
        assert row["stem"] == "first"

    asyncio.get_event_loop().run_until_complete(run())

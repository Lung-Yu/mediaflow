"""Tests for upload-related DB helpers."""
import asyncio
import os
import tempfile
from pathlib import Path

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


def test_schema_has_minio_columns(tmp_db):
    import aiosqlite

    async def check():
        async with aiosqlite.connect(tmp_db.DB_PATH) as db:
            cur = await db.execute("PRAGMA table_info(tasks)")
            cols = {row[1] for row in await cur.fetchall()}
        assert "minio_input_key" in cols
        assert "minio_output_prefix" in cols

    asyncio.get_event_loop().run_until_complete(check())


def test_get_task_returns_none_for_missing(tmp_db):
    result = asyncio.get_event_loop().run_until_complete(tmp_db.get_task("nonexistent"))
    assert result is None


def test_get_task_returns_dict_after_upsert(tmp_db):
    async def run():
        await tmp_db.upsert_task("s1", filename="s1.mp4", status="pending",
                                  minio_input_key="s1/s1.mp4")
        return await tmp_db.get_task("s1")

    task = asyncio.get_event_loop().run_until_complete(run())
    assert task["status"] == "pending"
    assert task["minio_input_key"] == "s1/s1.mp4"


def test_count_active_tasks(tmp_db):
    async def run():
        for stem, status in [("a", "downloading"), ("b", "processing"),
                               ("c", "completed"), ("d", "pending")]:
            await tmp_db.upsert_task(stem, status=status)
        return await tmp_db.count_active_tasks()

    count = asyncio.get_event_loop().run_until_complete(run())
    assert count == 2  # downloading + processing (completed and pending excluded)


def test_get_oldest_pending(tmp_db):
    import time

    async def run():
        t1 = time.time() - 10
        t2 = time.time()
        await tmp_db.upsert_task("old", status="pending", submitted_at=t1,
                                  minio_input_key="old/f.mp4", filename="f.mp4")
        await tmp_db.upsert_task("new", status="pending", submitted_at=t2,
                                  minio_input_key="new/f.mp4", filename="f.mp4")
        return await tmp_db.get_oldest_pending()

    task = asyncio.get_event_loop().run_until_complete(run())
    assert task["stem"] == "old"


def test_get_oldest_pending_returns_none_when_empty(tmp_db):
    result = asyncio.get_event_loop().run_until_complete(tmp_db.get_oldest_pending())
    assert result is None


def test_get_upload_queue_filters_by_minio_key(tmp_db):
    async def run():
        await tmp_db.upsert_task("with_minio", status="pending",
                                  minio_input_key="with_minio/f.mp4", filename="f.mp4")
        await tmp_db.upsert_task("no_minio", status="completed", filename="g.mp4")
        return await tmp_db.get_upload_queue()

    tasks = asyncio.get_event_loop().run_until_complete(run())
    stems = [t["stem"] for t in tasks]
    assert "with_minio" in stems
    assert "no_minio" not in stems


def test_delete_task(tmp_db):
    async def run():
        await tmp_db.upsert_task("to_delete", status="pending",
                                  minio_input_key="x/f.mp4", filename="f.mp4")
        await tmp_db.delete_task("to_delete")
        return await tmp_db.get_task("to_delete")

    result = asyncio.get_event_loop().run_until_complete(run())
    assert result is None

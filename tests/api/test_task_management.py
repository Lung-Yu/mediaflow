"""Tests for task management API — submission, rerun, delete."""
import asyncio
import os
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
        assert row["from_stage"] is None
        row2 = await tmp_db.pop_oldest_rerun()
        assert row2["stem"] == "second"
        assert row2["from_stage"] == "summarize"
        row3 = await tmp_db.pop_oldest_rerun()
        assert row3 is None

    asyncio.get_event_loop().run_until_complete(run())


# ── API endpoint fixtures ────────────────────────────────────────────────────

@pytest.fixture
def tasks_client(tmp_path, monkeypatch):
    """TestClient wired to a fresh DB and a real workspace/1_input/ dir."""
    db_file = str(tmp_path / "test.db")
    ws = tmp_path / "workspace"
    (ws / "1_input").mkdir(parents=True)

    monkeypatch.setenv("DB_PATH", db_file)
    monkeypatch.setenv("WORKSPACE_DIR", str(ws))

    import importlib
    import api.db as db_mod
    importlib.reload(db_mod)
    asyncio.get_event_loop().run_until_complete(db_mod.init())

    import api.routes.tasks as tasks_mod
    importlib.reload(tasks_mod)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(tasks_mod.router)
    return TestClient(app)


# ── POST /tasks ──────────────────────────────────────────────────────────────

def test_submit_valid_path_returns_201(tasks_client, tmp_path):
    src = tmp_path / "recording.m4a"
    src.write_bytes(b"fake audio")
    ws = tmp_path / "workspace"

    resp = tasks_client.post("/tasks", json={"path": str(src)})
    assert resp.status_code == 201
    data = resp.json()
    assert data["stem"] == "recording"
    assert data["status"] == "submitted"
    assert data["filename"] == "recording.m4a"
    assert (ws / "1_input" / "recording.m4a").exists()


def test_submit_missing_file_returns_404(tasks_client, tmp_path):
    resp = tasks_client.post("/tasks", json={"path": "/nonexistent/file.m4a"})
    assert resp.status_code == 404


def test_submit_unsupported_format_returns_415(tasks_client, tmp_path):
    src = tmp_path / "doc.pdf"
    src.write_bytes(b"fake pdf")
    resp = tasks_client.post("/tasks", json={"path": str(src)})
    assert resp.status_code == 415


def test_submit_conflict_returns_409(tasks_client, tmp_path):
    import api.db as db_mod
    src = tmp_path / "lesson.m4a"
    src.write_bytes(b"audio")
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("lesson", status="processing", filename="lesson.m4a")
    )
    resp = tasks_client.post("/tasks", json={"path": str(src)})
    assert resp.status_code == 409


def test_submit_stem_override(tasks_client, tmp_path):
    src = tmp_path / "recording.m4a"
    src.write_bytes(b"audio")
    resp = tasks_client.post("/tasks", json={"path": str(src), "stem": "custom_name"})
    assert resp.status_code == 201
    assert resp.json()["stem"] == "custom_name"


# ── POST /tasks/{stem}/runs ──────────────────────────────────────────────────

def test_rerun_inserts_db_row_and_returns_201(tasks_client):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("s1", status="failed", filename="s1.m4a")
    )
    resp = tasks_client.post("/tasks/s1/runs", json={"from_stage": "summarize"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["stem"] == "s1"
    assert data["from_stage"] == "summarize"
    assert data["status"] == "submitted"

    row = asyncio.get_event_loop().run_until_complete(db_mod.pop_oldest_rerun())
    assert row["stem"] == "s1"
    assert row["from_stage"] == "summarize"


def test_rerun_full_restart_omits_from_stage(tasks_client):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("s2", status="failed", filename="s2.m4a")
    )
    resp = tasks_client.post("/tasks/s2/runs", json={})
    assert resp.status_code == 201
    assert resp.json()["from_stage"] is None


def test_rerun_unknown_stage_returns_422(tasks_client):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("s3", status="failed", filename="s3.m4a")
    )
    resp = tasks_client.post("/tasks/s3/runs", json={"from_stage": "nonexistent"})
    assert resp.status_code == 422


def test_rerun_unknown_task_returns_404(tasks_client):
    resp = tasks_client.post("/tasks/ghost/runs", json={})
    assert resp.status_code == 404


def test_rerun_active_task_returns_409(tasks_client):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("active_s", status="processing", filename="active_s.m4a")
    )
    resp = tasks_client.post("/tasks/active_s/runs", json={})
    assert resp.status_code == 409


# ── DELETE /tasks/{stem} ─────────────────────────────────────────────────────

def test_delete_removes_db_row(tasks_client):
    import api.db as db_mod
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("to_del", status="failed", filename="to_del.m4a")
    )
    resp = tasks_client.delete("/tasks/to_del")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": "to_del"}
    task = asyncio.get_event_loop().run_until_complete(db_mod.get_task("to_del"))
    assert task is None


def test_delete_removes_file_from_input(tasks_client, tmp_path):
    import api.db as db_mod
    ws = Path(os.environ["WORKSPACE_DIR"])
    f = ws / "1_input" / "queued.m4a"
    f.write_bytes(b"audio")
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("queued", status="submitted", filename="queued.m4a")
    )
    tasks_client.delete("/tasks/queued")
    assert not f.exists()


def test_delete_unknown_returns_404(tasks_client):
    resp = tasks_client.delete("/tasks/ghost")
    assert resp.status_code == 404

"""Tests for timeline DB helper and route logic."""
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


def test_get_stage_events_empty(tmp_db):
    result = asyncio.get_event_loop().run_until_complete(
        tmp_db.get_stage_events("nostem")
    )
    assert result == []


def test_get_stage_events_returns_stage_completed_events(tmp_db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(tmp_db.insert_event(
        "s1", "stage.completed", stage="preprocess", ts=1000.0, status="", payload="{}"
    ))
    loop.run_until_complete(tmp_db.insert_event(
        "s1", "task.submitted", stage="", ts=990.0, status="", payload="{}"
    ))
    loop.run_until_complete(tmp_db.insert_event(
        "s1", "stage.completed", stage="transcribe", ts=1120.0, status="", payload="{}"
    ))
    result = loop.run_until_complete(tmp_db.get_stage_events("s1"))
    assert len(result) == 2
    assert result[0]["stage"] == "preprocess"
    assert result[0]["ts"] == 1000.0
    assert result[1]["stage"] == "transcribe"
    assert result[1]["ts"] == 1120.0


def test_get_timeline_returns_404_for_missing_stem(tmp_db):
    import api.routes.tasks as tasks_mod
    import asyncio
    from unittest.mock import patch
    from fastapi import HTTPException

    async def run():
        with patch.object(tasks_mod, "db", tmp_db):
            try:
                await tasks_mod.get_timeline("nonexistent")
                return None
            except HTTPException as e:
                return e.status_code

    code = asyncio.get_event_loop().run_until_complete(run())
    assert code == 404


def test_get_timeline_computes_stage_durations(tmp_db):
    import api.routes.tasks as tasks_mod
    from unittest.mock import patch

    loop = asyncio.get_event_loop()

    loop.run_until_complete(tmp_db.upsert_task(
        "lesson01",
        filename="lesson01.m4a",
        status="completed",
        submitted_at=1000.0,
        started_at=1023.0,
        completed_at=1215.0,
        duration_sec=215.0,
    ))
    loop.run_until_complete(tmp_db.insert_event(
        "lesson01", "stage.completed", stage="preprocess", ts=1023.0, status="", payload="{}"
    ))
    loop.run_until_complete(tmp_db.insert_event(
        "lesson01", "stage.completed", stage="transcribe", ts=1180.0, status="", payload="{}"
    ))
    loop.run_until_complete(tmp_db.insert_event(
        "lesson01", "stage.completed", stage="summarize", ts=1215.0, status="", payload="{}"
    ))

    async def run():
        with patch.object(tasks_mod, "db", tmp_db):
            return await tasks_mod.get_timeline("lesson01")

    result = loop.run_until_complete(run())
    assert result["stem"] == "lesson01"
    assert result["filename"] == "lesson01.m4a"
    assert result["submitted_at"] == 1000.0
    assert result["total_wall_sec"] == 215
    stages = result["stages"]
    assert len(stages) == 3
    # preprocess: 1023 - 1000 = 23s
    assert stages[0]["stage"] == "preprocess"
    assert stages[0]["duration_sec"] == 23
    # transcribe: 1180 - 1023 = 157s
    assert stages[1]["stage"] == "transcribe"
    assert stages[1]["duration_sec"] == 157
    # summarize: 1215 - 1180 = 35s
    assert stages[2]["stage"] == "summarize"
    assert stages[2]["duration_sec"] == 35
    assert result["total_pipeline_sec"] == 23 + 157 + 35

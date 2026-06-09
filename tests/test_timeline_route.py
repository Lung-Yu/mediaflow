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

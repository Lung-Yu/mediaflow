import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

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


def test_get_task_aggregates_empty(tmp_db):
    result = asyncio.get_event_loop().run_until_complete(
        tmp_db.get_task_aggregates()
    )
    assert result["total_tasks"] == 0
    assert result["total_duration_sec"] == 0.0
    assert result["completed"] == 0


def test_get_task_aggregates_counts(tmp_db):
    loop = asyncio.get_event_loop()
    loop.run_until_complete(tmp_db.upsert_task(
        "s1", filename="s1.m4a", status="completed", duration_sec=120.0
    ))
    loop.run_until_complete(tmp_db.upsert_task(
        "s2", filename="s2.m4a", status="failed", duration_sec=30.0
    ))
    result = loop.run_until_complete(tmp_db.get_task_aggregates())
    assert result["total_tasks"] == 2
    assert result["total_duration_sec"] == 150.0
    assert result["completed"] == 1


def test_speaker_totals_empty_dir():
    import api.routes.stats as stats_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        result = stats_mod._speaker_totals(Path(tmpdir))
    assert result == []


def test_speaker_totals_aggregates_across_files():
    import api.routes.stats as stats_mod
    diar1 = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 10.0},
        {"speaker": "SPEAKER_01", "start": 10.0, "end": 16.0},
    ]
    diar2 = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.0},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "a_diarization.json").write_text(json.dumps(diar1), encoding="utf-8")
        (tmp / "b_diarization.json").write_text(json.dumps(diar2), encoding="utf-8")
        result = stats_mod._speaker_totals(tmp)
    # SPEAKER_00: 10 + 5 = 15s, SPEAKER_01: 6s, total 21s
    assert result[0]["label"] == "SPEAKER_00"
    assert result[0]["seconds"] == 15.0
    assert abs(result[0]["pct"] - 15 / 21) < 0.01
    assert result[1]["label"] == "SPEAKER_01"


def test_keyword_counts_empty_dir():
    import api.routes.stats as stats_mod
    with tempfile.TemporaryDirectory() as tmpdir:
        result = stats_mod._keyword_counts(Path(tmpdir))
    assert result == []


def test_keyword_counts_top_10():
    import api.routes.stats as stats_mod
    summaries = [
        {"topic_segments": [{"topic": "機器學習"}, {"topic": "神經網路"}, {"topic": "機器學習"}]},
        {"topic_segments": [{"topic": "機器學習"}, {"topic": "深度學習"}]},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        for i, s in enumerate(summaries):
            (tmp / f"file{i}_summary.json").write_text(json.dumps(s), encoding="utf-8")
        result = stats_mod._keyword_counts(tmp)
    assert result[0] == {"topic": "機器學習", "count": 3}
    assert len(result) <= 10

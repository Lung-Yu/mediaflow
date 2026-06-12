import asyncio
import os
import time

import pytest

os.environ.setdefault("DB_PATH", ":memory:")


@pytest.fixture(autouse=True)
def reset_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    import importlib
    import api.db as db_mod
    importlib.reload(db_mod)
    asyncio.get_event_loop().run_until_complete(db_mod.init())


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _event(event, stem="s1", stage="", **kw):
    return {"event": event, "stem": stem, "stage": stage, "ts": str(time.time()), **kw}


def test_stage_started_preprocess_sets_preprocessing():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    status = _run(ep.process_event(_event("stage.started", stage="preprocess")))
    assert status == "preprocessing"


def test_stage_started_transcribe_sets_transcribing():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    status = _run(ep.process_event(_event("stage.started", stage="transcribe")))
    assert status == "transcribing"


def test_stage_started_summarize_sets_summarizing():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    status = _run(ep.process_event(_event("stage.started", stage="summarize")))
    assert status == "summarizing"


def test_stage_started_all_known_stages():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    expected = {
        "preprocess":      "preprocessing",
        "transcribe":      "transcribing",
        "verify_segments": "verifying",
        "correct_srt":     "correcting",
        "diarize":         "diarizing",
        "summarize":       "summarizing",
        "detect_chapters": "detecting_chapters",
    }
    for stage, want in expected.items():
        got = _run(ep.process_event(_event("stage.started", stem=stage, stage=stage)))
        assert got == want, f"stage={stage}: expected {want!r}, got {got!r}"


def test_stage_completed_does_not_change_db_status():
    import importlib
    import api.db as db_mod
    import api.event_processor as ep
    importlib.reload(ep)

    # Set status to transcribing via stage.started
    _run(ep.process_event(_event("stage.started", stage="transcribe")))
    row_before = _run(db_mod.get_task("s1"))
    assert row_before["status"] == "transcribing"

    # stage.completed must NOT change the status
    _run(ep.process_event(_event("stage.completed", stage="transcribe")))
    row_after = _run(db_mod.get_task("s1"))
    assert row_after["status"] == "transcribing"


def test_task_completed_sets_completed():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    status = _run(ep.process_event(_event("task.completed")))
    assert status == "completed"


def test_task_failed_sets_failed():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    status = _run(ep.process_event(_event("task.failed", error_msg="boom")))
    assert status == "failed"


def test_unknown_stage_started_falls_back_to_processing():
    import importlib
    import api.event_processor as ep
    importlib.reload(ep)
    status = _run(ep.process_event(_event("stage.started", stage="unknown_future_stage")))
    assert status == "processing"


def test_get_status_overview_includes_transcribing_tasks():
    import importlib
    import api.db as db_mod
    import api.event_processor as ep
    importlib.reload(ep)
    importlib.reload(db_mod)

    # Task enters transcribing state
    _run(ep.process_event(_event("task.submitted", filename="a.m4a")))
    _run(ep.process_event(_event("stage.started", stage="transcribe")))

    overview = _run(db_mod.get_status_overview())
    assert any(t["stem"] == "s1" for t in overview["processing"]), \
        "transcribing task must appear in processing list"


def test_get_status_overview_includes_all_active_stages():
    import importlib
    import api.db as db_mod
    import api.event_processor as ep
    importlib.reload(ep)
    importlib.reload(db_mod)

    active_stages = [
        "preprocess", "transcribe", "verify_segments",
        "correct_srt", "diarize", "summarize", "detect_chapters",
    ]
    for stage in active_stages:
        _run(ep.process_event(_event("stage.started", stem=stage, stage=stage)))

    overview = _run(db_mod.get_status_overview())
    stems_in_processing = {t["stem"] for t in overview["processing"]}
    for stage in active_stages:
        assert stage in stems_in_processing, f"stem={stage} (stage status) missing from processing"

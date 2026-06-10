"""Tests for api/lifecycle.py (parse_retention) and api/cleanup.py (output cleanup)."""
import asyncio
import os
import time
from datetime import timedelta
from pathlib import Path

import pytest

os.environ.setdefault("DB_PATH", ":memory:")


# ── api/lifecycle.py ──────────────────────────────────────────────────────────

from api.lifecycle import parse_retention as api_parse


def test_api_parse_retention_days():
    assert api_parse("30d") == timedelta(days=30)
    assert api_parse("7d") == timedelta(days=7)


def test_api_parse_retention_forever():
    assert api_parse("forever") is None
    assert api_parse("keep") is None
    assert api_parse("") is None


def test_api_parse_retention_immediate():
    assert api_parse("immediate") == timedelta(0)


def test_api_parse_retention_invalid_returns_none():
    assert api_parse("badvalue") is None


# ── api/cleanup.py ────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_output(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test.db")
    monkeypatch.setenv("DB_PATH", db_file)
    import importlib
    import api.db as db_mod
    importlib.reload(db_mod)
    asyncio.get_event_loop().run_until_complete(db_mod.init())
    output_dir = tmp_path / "3_output"
    output_dir.mkdir()
    return db_mod, output_dir


def _age_file(path: Path, days: int) -> None:
    ts = time.time() - days * 86400
    os.utime(path, (ts, ts))


def _make_stem(output_dir: Path, stem: str, age_days: int = 0) -> None:
    srt = output_dir / f"{stem}.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    (output_dir / f"{stem}_summary.md").write_text("# Summary")
    if age_days > 0:
        _age_file(srt, age_days)
        _age_file(output_dir / f"{stem}_summary.md", age_days)


def test_output_cleanup_deletes_all_stem_files(tmp_output):
    db_mod, output_dir = tmp_output
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("old_lesson", status="completed", filename="old_lesson.m4a")
    )
    _make_stem(output_dir, "old_lesson", age_days=40)

    from api.cleanup import run_output_cleanup
    asyncio.get_event_loop().run_until_complete(
        run_output_cleanup(output_dir, timedelta(days=30))
    )
    assert not (output_dir / "old_lesson.srt").exists()
    assert not (output_dir / "old_lesson_summary.md").exists()


def test_output_cleanup_removes_db_row(tmp_output):
    db_mod, output_dir = tmp_output
    asyncio.get_event_loop().run_until_complete(
        db_mod.upsert_task("old_lesson2", status="completed", filename="old_lesson2.m4a")
    )
    _make_stem(output_dir, "old_lesson2", age_days=40)

    from api.cleanup import run_output_cleanup
    asyncio.get_event_loop().run_until_complete(
        run_output_cleanup(output_dir, timedelta(days=30))
    )
    task = asyncio.get_event_loop().run_until_complete(db_mod.get_task("old_lesson2"))
    assert task is None


def test_output_cleanup_skips_fresh_stem(tmp_output):
    _, output_dir = tmp_output
    _make_stem(output_dir, "fresh", age_days=0)

    from api.cleanup import run_output_cleanup
    asyncio.get_event_loop().run_until_complete(
        run_output_cleanup(output_dir, timedelta(days=30))
    )
    assert (output_dir / "fresh.srt").exists()


def test_output_cleanup_tolerates_missing_files(tmp_output):
    _, output_dir = tmp_output
    _make_stem(output_dir, "partial", age_days=40)
    (output_dir / "partial_summary.md").unlink()  # simulate already-gone file

    from api.cleanup import run_output_cleanup
    # Must not raise
    asyncio.get_event_loop().run_until_complete(
        run_output_cleanup(output_dir, timedelta(days=30))
    )


def test_output_cleanup_noop_for_forever(tmp_output):
    _, output_dir = tmp_output
    _make_stem(output_dir, "keep_me", age_days=400)

    from api.cleanup import run_output_cleanup
    asyncio.get_event_loop().run_until_complete(
        run_output_cleanup(output_dir, None)
    )
    assert (output_dir / "keep_me.srt").exists()

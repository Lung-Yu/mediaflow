"""Tests for pipeline/lifecycle.py — parse_retention and scan_and_expire."""
import os
import time
from datetime import timedelta
from pathlib import Path

from pipeline.lifecycle import parse_retention, scan_and_expire


def test_parse_retention_immediate():
    assert parse_retention("immediate") == timedelta(0)


def test_parse_retention_days():
    assert parse_retention("30d") == timedelta(days=30)
    assert parse_retention("90d") == timedelta(days=90)


def test_parse_retention_forever():
    assert parse_retention("forever") is None
    assert parse_retention("keep") is None
    assert parse_retention("") is None


def test_scan_and_expire_deletes_old_file(tmp_path):
    f = tmp_path / "old_clean.wav"
    f.write_bytes(b"data")
    old_ts = time.time() - 40 * 86400
    os.utime(f, (old_ts, old_ts))
    deleted = scan_and_expire(tmp_path, timedelta(days=30))
    assert f in deleted
    assert not f.exists()


def test_scan_and_expire_skips_fresh_file(tmp_path):
    f = tmp_path / "new_clean.wav"
    f.write_bytes(b"data")
    deleted = scan_and_expire(tmp_path, timedelta(days=30))
    assert deleted == []
    assert f.exists()


def test_scan_and_expire_tolerates_missing_file(tmp_path):
    # Directory exists but no matching files — must not raise
    deleted = scan_and_expire(tmp_path, timedelta(0))
    assert deleted == []


def test_scan_and_expire_dry_run_no_delete(tmp_path):
    f = tmp_path / "old.wav"
    f.write_bytes(b"data")
    old_ts = time.time() - 10 * 86400
    os.utime(f, (old_ts, old_ts))
    deleted = scan_and_expire(tmp_path, timedelta(days=5), dry_run=True)
    assert f in deleted
    assert f.exists()  # not actually deleted


def test_scan_and_expire_forever_returns_empty(tmp_path):
    f = tmp_path / "old.wav"
    f.write_bytes(b"data")
    deleted = scan_and_expire(tmp_path, retention=None)
    assert deleted == []
    assert f.exists()


def test_scan_and_expire_nonexistent_dir():
    # Missing directory must not raise
    deleted = scan_and_expire(Path("/nonexistent/dir/xyz"), timedelta(days=1))
    assert deleted == []


def test_scan_and_expire_stem_pattern(tmp_path):
    wav = tmp_path / "stem_clean.wav"
    mp4 = tmp_path / "stem.mp4"
    for f in [wav, mp4]:
        f.write_bytes(b"data")
        old_ts = time.time() - 60 * 86400
        os.utime(f, (old_ts, old_ts))
    deleted = scan_and_expire(tmp_path, timedelta(days=30), stem_pattern="*_clean.wav")
    assert wav in deleted
    assert mp4 not in deleted
    assert mp4.exists()

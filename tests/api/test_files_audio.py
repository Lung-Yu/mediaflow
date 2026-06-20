"""Unit tests for audio file API additions."""
import tempfile
from pathlib import Path
from unittest.mock import patch

import api.routes.files as files_module
from fastapi.testclient import TestClient
from fastapi import FastAPI

app = FastAPI()
app.include_router(files_module.router)
client = TestClient(app)


# ── audio endpoint ───────────────────────────────────────────────

def test_audio_returns_404_when_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(files_module, "PROCESSING_DIR", Path(tmpdir)):
            r = client.get("/files/nostem/audio")
    assert r.status_code == 404


def test_audio_returns_file_when_present():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        wav = tmp / "mystem_clean.wav"
        wav.write_bytes(b"RIFF" + b"\x00" * 36)  # minimal fake WAV header
        with patch.object(files_module, "PROCESSING_DIR", tmp):
            r = client.get("/files/mystem/audio")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/")


# ── start_seconds in segments ────────────────────────────────────

def test_segments_include_start_seconds():
    srt_content = (
        "1\n00:00:01,000 --> 00:00:03,000\nhello world\n\n"
        "2\n00:01:00,500 --> 00:01:02,000\nfoo bar\n\n"
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "s1.srt").write_text(srt_content, encoding="utf-8")
        with patch.object(files_module, "OUTPUT_DIR", tmp):
            r = client.get("/files/s1/segments")
    assert r.status_code == 200
    segs = r.json()
    assert segs[0]["start_seconds"] == 1.0
    assert abs(segs[1]["start_seconds"] - 60.5) < 0.001


# ── has_audio in speaker-names ───────────────────────────────────

def test_speaker_names_has_audio_false_when_wav_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        with (
            patch.object(files_module, "OUTPUT_DIR", Path(tmpdir)),
            patch.object(files_module, "PROCESSING_DIR", Path(tmpdir)),
        ):
            result = files_module.get_speaker_names("nostem")
    assert result["has_audio"] is False


def test_speaker_names_has_audio_true_when_wav_present():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "s1_clean.wav").write_bytes(b"RIFF")
        with (
            patch.object(files_module, "OUTPUT_DIR", tmp),
            patch.object(files_module, "PROCESSING_DIR", tmp),
        ):
            result = files_module.get_speaker_names("s1")
    assert result["has_audio"] is True

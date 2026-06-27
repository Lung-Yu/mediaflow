"""Verify /transcribe_segments returns the exact format stages.py expects.

Requires the asr service running on :9004:
  uvicorn asr.service:app --port 9004

Tests that require a loaded model are skipped when the model is not downloaded.
"""
import io
import struct
import wave

import httpx
import pytest


def _make_silence_wav(duration_sec: float = 2.0, sr: int = 16000) -> bytes:
    buf = io.BytesIO()
    n = int(sr * duration_sec)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(struct.pack(f"<{n}h", *([0] * n)))
    return buf.getvalue()


def _model_loaded() -> bool:
    try:
        resp = httpx.get("http://localhost:9004/health", timeout=5)
        return resp.status_code == 200 and resp.json().get("model_loaded", False)
    except Exception:
        return False


@pytest.mark.skipif(not _model_loaded(), reason="model not downloaded — skipping transcription format tests")
def test_health():
    resp = httpx.get("http://localhost:9004/health", timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "model_loaded" in body
    assert "model" in body


@pytest.mark.skipif(not _model_loaded(), reason="model not downloaded — skipping transcription format tests")
def test_transcribe_segments_format():
    wav = _make_silence_wav()
    resp = httpx.post(
        "http://localhost:9004/transcribe_segments",
        files={"audio": ("test.wav", wav, "audio/wav")},
        params={"language": "zh"},
        timeout=120,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "segments" in body
    for seg in body["segments"]:
        assert isinstance(seg["id"], int)
        assert isinstance(seg["start"], float)
        assert isinstance(seg["end"], float)
        assert isinstance(seg["text"], str)
        assert isinstance(seg["avg_logprob"], float)
        assert isinstance(seg["no_speech_prob"], float)


@pytest.mark.skipif(not _model_loaded(), reason="model not downloaded — skipping transcription format tests")
def test_transcribe_large_format():
    wav = _make_silence_wav()
    resp = httpx.post(
        "http://localhost:9004/transcribe_large",
        files={"audio": ("test.wav", wav, "audio/wav")},
        params={"language": "zh"},
        timeout=120,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "text" in body
    assert isinstance(body["text"], str)

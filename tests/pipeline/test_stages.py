import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pipeline.stages import _assign_speaker, diarize


def _make_srt(blocks):
    """blocks: list of (start_hms, end_hms, text) — times as HH:MM:SS"""
    lines = []
    for i, (start, end, text) in enumerate(blocks, start=1):
        lines.append(f"{i}\n{start},000 --> {end},000\n{text}\n")
    return "\n".join(lines)


def test_assign_speaker_returns_dominant_speaker():
    segs = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 3.0},
        {"speaker": "SPEAKER_01", "start": 3.0, "end": 6.0},
    ]
    assert _assign_speaker(0.5, 2.5, segs) == "SPEAKER_00"
    assert _assign_speaker(3.5, 5.5, segs) == "SPEAKER_01"


def test_assign_speaker_picks_largest_overlap():
    segs = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0},
        {"speaker": "SPEAKER_01", "start": 2.0, "end": 5.0},
    ]
    # block 1.0–4.0: 1 s with SPEAKER_00, 2 s with SPEAKER_01
    assert _assign_speaker(1.0, 4.0, segs) == "SPEAKER_01"


def test_assign_speaker_returns_none_when_no_overlap():
    segs = [{"speaker": "SPEAKER_00", "start": 5.0, "end": 8.0}]
    assert _assign_speaker(0.0, 2.0, segs) is None


def test_assign_speaker_exact_boundary():
    segs = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0},
        {"speaker": "SPEAKER_01", "start": 2.0, "end": 4.0},
    ]
    assert _assign_speaker(2.0, 3.0, segs) == "SPEAKER_01"


# ── diarize() tests ──────────────────────────────────────────────────────────

def test_diarize_enriches_srt_with_speaker_labels():
    cfg = {
        "diarization": {
            "service_url": "http://localhost:9003",
            "speaker_format": "【{speaker}】",
        }
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "segments": [
            {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.0},
            {"speaker": "SPEAKER_01", "start": 5.0, "end": 10.0},
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        srt_path = tmp / "test.srt"
        audio_path = tmp / "test_clean.wav"
        audio_path.write_bytes(b"RIFF" + b"\x00" * 36)
        srt_path.write_text(
            _make_srt([("00:00:01", "00:00:03", "Hello"), ("00:00:06", "00:00:08", "World")]),
            encoding="utf-8",
        )

        with patch("pipeline.stages.httpx.post", return_value=mock_resp):
            result = diarize("test", srt_path, audio_path, cfg)

        srt_out = srt_path.read_text(encoding="utf-8")
        assert "【SPEAKER_00】Hello" in srt_out
        assert "【SPEAKER_01】World" in srt_out
        assert result == tmp / "test_diarization.json"
        assert len(json.loads(result.read_text())) == 2


def test_diarize_passes_whisper_segments_to_service():
    cfg = {"diarization": {"service_url": "http://localhost:9003"}}
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"segments": []}
    mock_resp.raise_for_status = MagicMock()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        srt_path = tmp / "test.srt"
        audio_path = tmp / "test_clean.wav"
        audio_path.write_bytes(b"RIFF" + b"\x00" * 36)
        srt_path.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello\n", encoding="utf-8")
        seg_json = tmp / "test_segments.json"
        seg_json.write_text(
            json.dumps([{"start": 1.0, "end": 3.0, "text": "Hello"}]),
            encoding="utf-8",
        )

        with patch("pipeline.stages.httpx.post", return_value=mock_resp) as mock_post:
            diarize("test", srt_path, audio_path, cfg)

        call_data = mock_post.call_args
        assert "data" in call_data.kwargs
        sent_segs = json.loads(call_data.kwargs["data"]["segments"])
        assert sent_segs == [{"start": 1.0, "end": 3.0}]


def test_diarize_skips_gracefully_when_service_unavailable():
    import httpx as real_httpx

    cfg = {"diarization": {"service_url": "http://localhost:9003"}}

    with tempfile.TemporaryDirectory() as tmpdir:
        srt_path = Path(tmpdir) / "test.srt"
        audio_path = Path(tmpdir) / "test_clean.wav"
        audio_path.write_bytes(b"RIFF" + b"\x00" * 36)
        original = "1\n00:00:01,000 --> 00:00:03,000\nHello\n"
        srt_path.write_text(original, encoding="utf-8")

        with patch("pipeline.stages.httpx.post", side_effect=real_httpx.ConnectError("refused")):
            diarize("test", srt_path, audio_path, cfg)

        assert srt_path.read_text(encoding="utf-8") == original


def test_diarize_applies_speaker_name_mapping():
    cfg = {
        "diarization": {
            "service_url": "http://localhost:9003",
            "speaker_format": "【{speaker}】",
            "speaker_names": {"SPEAKER_00": "老師"},
        }
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "segments": [{"speaker": "SPEAKER_00", "start": 0.0, "end": 5.0}]
    }
    mock_resp.raise_for_status = MagicMock()

    with tempfile.TemporaryDirectory() as tmpdir:
        srt_path = Path(tmpdir) / "test.srt"
        audio_path = Path(tmpdir) / "test_clean.wav"
        audio_path.write_bytes(b"RIFF" + b"\x00" * 36)
        srt_path.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello\n", encoding="utf-8")

        with patch("pipeline.stages.httpx.post", return_value=mock_resp):
            diarize("test", srt_path, audio_path, cfg)

        assert "【老師】Hello" in srt_path.read_text(encoding="utf-8")


def test_diarize_skips_when_audio_missing():
    cfg = {"diarization": {"service_url": "http://localhost:9003"}}

    with tempfile.TemporaryDirectory() as tmpdir:
        srt_path = Path(tmpdir) / "test.srt"
        audio_path = Path(tmpdir) / "nonexistent.wav"
        original = "1\n00:00:01,000 --> 00:00:03,000\nHello\n"
        srt_path.write_text(original, encoding="utf-8")

        with patch("pipeline.stages.httpx.post") as mock_post:
            diarize("test", srt_path, audio_path, cfg)
            mock_post.assert_not_called()

        assert srt_path.read_text(encoding="utf-8") == original

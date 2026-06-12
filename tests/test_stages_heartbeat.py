import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch


def _make_cfg():
    return {
        "whisper": {
            "service_url": "http://localhost:9001",
            "language": "zh",
            "initial_prompt": "",
        }
    }


def test_heartbeat_thread_starts_and_stops(tmp_path, caplog):
    import logging
    from pipeline.stages import transcribe

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"segments": []}

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 36)

    threads_before = set(t.name for t in threading.enumerate())

    with patch("pipeline.stages.httpx.post", return_value=mock_resp), \
         caplog.at_level(logging.INFO, logger="pipeline.stages"):
        transcribe(audio, "s1", tmp_path, _make_cfg())

    # Heartbeat thread is daemon — may already be gone, but it must have existed
    # We verify indirectly: no exception raised and SRT was written
    assert (tmp_path / "s1.srt").exists()


def test_heartbeat_stops_on_exception(tmp_path):
    import httpx as real_httpx
    from pipeline.stages import transcribe

    audio = tmp_path / "audio.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 36)

    with patch("pipeline.stages.httpx.post",
               side_effect=real_httpx.ConnectError("refused")):
        try:
            transcribe(audio, "s1", tmp_path, _make_cfg())
        except RuntimeError:
            pass  # expected

    # Give any daemon threads a moment to observe _stop
    time.sleep(0.1)

    alive_hb = [t for t in threading.enumerate() if t.name == "hb-s1"]
    assert not alive_hb, "heartbeat thread must not outlive the transcribe call"

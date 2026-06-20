from unittest.mock import MagicMock, patch
from pathlib import Path

from pipeline.runner import execute


def _cfg(stop_after=None):
    cfg = {
        "pipeline": {
            "stages": [
                {"id": "preprocess",  "enabled": True},
                {"id": "transcribe",  "enabled": True},
                {"id": "summarize",   "enabled": True},
            ]
        }
    }
    if stop_after:
        cfg["pipeline"]["stop_after_stage"] = stop_after
    return cfg


def _ctx(tmp_path):
    audio = tmp_path / "s1_clean.wav"
    audio.write_bytes(b"RIFF" + b"\x00" * 36)
    srt = tmp_path / "s1.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n", encoding="utf-8")
    input_path = tmp_path / "s1.m4a"
    input_path.write_bytes(b"\x00")
    return {
        "stem": "s1",
        "workspace": tmp_path,
        "output_dir": tmp_path,
        "input_path": input_path,
        "audio_path": audio,
        "srt_path": srt,
    }


def test_stop_after_transcribe_skips_summarize(tmp_path):
    pub = MagicMock()
    ctx = _ctx(tmp_path)

    with patch("pipeline.runner.stages.preprocess", return_value=ctx["audio_path"]), \
         patch("pipeline.runner.stages.transcribe", return_value=ctx["srt_path"]), \
         patch("pipeline.runner.stages.summarize") as mock_sum:
        execute(_cfg(), ctx, pub, stop_after="transcribe")

    mock_sum.assert_not_called()


def test_stop_after_publishes_completed_event_for_stop_stage(tmp_path):
    pub = MagicMock()
    ctx = _ctx(tmp_path)

    with patch("pipeline.runner.stages.preprocess", return_value=ctx["audio_path"]), \
         patch("pipeline.runner.stages.transcribe", return_value=ctx["srt_path"]), \
         patch("pipeline.runner.stages.summarize"):
        execute(_cfg(), ctx, pub, stop_after="transcribe")

    completed_calls = [c for c in pub.publish.call_args_list
                       if c.args[0] == "stage.completed" and c.kwargs.get("stage") == "transcribe"]
    assert completed_calls, "stage.completed/transcribe must be published even when stop_after fires"


def test_no_stop_after_runs_all_stages(tmp_path):
    pub = MagicMock()
    ctx = _ctx(tmp_path)

    with patch("pipeline.runner.stages.preprocess", return_value=ctx["audio_path"]), \
         patch("pipeline.runner.stages.transcribe", return_value=ctx["srt_path"]), \
         patch("pipeline.runner.stages.summarize", return_value=tmp_path / "s1_summary.md") as mock_sum:
        execute(_cfg(), ctx, pub, stop_after=None)

    mock_sum.assert_called_once()


def test_stop_after_disabled_stage_runs_to_completion(tmp_path):
    """If stop_after names a disabled stage, all enabled stages run."""
    pub = MagicMock()
    ctx = _ctx(tmp_path)
    cfg = {
        "pipeline": {
            "stages": [
                {"id": "preprocess",      "enabled": True},
                {"id": "correct_srt",     "enabled": False},  # disabled — break never fires
                {"id": "transcribe",      "enabled": True},
                {"id": "summarize",       "enabled": True},
            ]
        }
    }

    with patch("pipeline.runner.stages.preprocess", return_value=ctx["audio_path"]), \
         patch("pipeline.runner.stages.transcribe", return_value=ctx["srt_path"]), \
         patch("pipeline.runner.stages.summarize", return_value=tmp_path / "s1_summary.md") as mock_sum:
        execute(cfg, ctx, pub, stop_after="correct_srt")

    mock_sum.assert_called_once()

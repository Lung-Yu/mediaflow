"""Unit tests for speaker-names API functions."""
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import api.routes.files as files_module


def test_get_speaker_names_empty():
    with tempfile.TemporaryDirectory() as tmpdir:
        with (
            patch.object(files_module, "OUTPUT_DIR", Path(tmpdir)),
            patch.object(files_module, "PROCESSING_DIR", Path(tmpdir)),
        ):
            result = files_module.get_speaker_names("test")
    assert result == {"speakers": [], "counts": {}, "names": {}, "has_audio": False}


def test_get_speaker_names_reads_diarization_json():
    diar = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0},
        {"speaker": "SPEAKER_01", "start": 2.0, "end": 4.0},
        {"speaker": "SPEAKER_00", "start": 4.0, "end": 6.0},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "test_diarization.json").write_text(json.dumps(diar), encoding="utf-8")
        with patch.object(files_module, "OUTPUT_DIR", tmp):
            result = files_module.get_speaker_names("test")
    assert result["speakers"] == ["SPEAKER_00", "SPEAKER_01"]
    assert result["counts"] == {"SPEAKER_00": 2, "SPEAKER_01": 1}
    assert result["names"] == {}


def test_get_speaker_names_merges_saved_names():
    diar = [{"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0}]
    saved_names = {"SPEAKER_00": "老師"}
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "test_diarization.json").write_text(json.dumps(diar), encoding="utf-8")
        (tmp / "test_speaker_names.json").write_text(json.dumps(saved_names), encoding="utf-8")
        with patch.object(files_module, "OUTPUT_DIR", tmp):
            result = files_module.get_speaker_names("test")
    assert result["names"] == {"SPEAKER_00": "老師"}
    assert result["speakers"] == ["SPEAKER_00"]


def test_set_speaker_names_saves_file():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        with patch.object(files_module, "OUTPUT_DIR", tmp):
            result = files_module.set_speaker_names(
                "test", {"SPEAKER_00": "老師", "SPEAKER_01": "學生"}
            )
        saved = json.loads((tmp / "test_speaker_names.json").read_text(encoding="utf-8"))
    assert result["saved"] == 2
    assert saved == {"SPEAKER_00": "老師", "SPEAKER_01": "學生"}


def test_set_speaker_names_ignores_empty_values():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        with patch.object(files_module, "OUTPUT_DIR", tmp):
            result = files_module.set_speaker_names(
                "test", {"SPEAKER_00": "老師", "SPEAKER_01": ""}
            )
        saved = json.loads((tmp / "test_speaker_names.json").read_text(encoding="utf-8"))
    assert result["saved"] == 1
    assert "SPEAKER_01" not in saved


def test_set_then_get_roundtrip():
    diar = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0},
        {"speaker": "SPEAKER_01", "start": 2.0, "end": 4.0},
    ]
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "test_diarization.json").write_text(json.dumps(diar), encoding="utf-8")
        with patch.object(files_module, "OUTPUT_DIR", tmp):
            files_module.set_speaker_names("test", {"SPEAKER_00": "老師"})
            result = files_module.get_speaker_names("test")
    assert result["names"] == {"SPEAKER_00": "老師"}
    assert result["speakers"] == ["SPEAKER_00", "SPEAKER_01"]

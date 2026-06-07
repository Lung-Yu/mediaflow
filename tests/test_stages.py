import pytest
from pipeline.stages import _assign_speaker


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

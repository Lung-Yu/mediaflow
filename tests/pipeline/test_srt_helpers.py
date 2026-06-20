import api.utils.srt as srtlib


def test_to_seconds_zero():
    assert srtlib.to_seconds("00:00:00,000") == 0.0


def test_to_seconds_minutes():
    assert srtlib.to_seconds("00:01:30,000") == 90.0


def test_to_seconds_hours():
    assert srtlib.to_seconds("01:00:00,000") == 3600.0


def test_to_seconds_milliseconds():
    assert abs(srtlib.to_seconds("00:00:01,500") - 1.5) < 0.001


def test_to_seconds_full():
    # 1h 2m 3.456s
    assert abs(srtlib.to_seconds("01:02:03,456") - 3723.456) < 0.001


def test_to_seconds_dot_separator():
    # Some SRT files use "." instead of ","
    assert srtlib.to_seconds("00:00:02.500") == 2.5

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../monitoring'))

from gpu_exporter import _parse_first_json


def test_parses_single_object():
    text = '{"gpu": {"idle_ratio": 0.9}, "processor": {"gpu_energy": 2.5}}'
    data = _parse_first_json(text)
    assert data["gpu"]["idle_ratio"] == 0.9
    assert data["processor"]["gpu_energy"] == 2.5


def test_parses_first_of_multiple_objects():
    text = '{"gpu": {"idle_ratio": 0.8}}\n{"gpu": {"idle_ratio": 0.7}}'
    data = _parse_first_json(text)
    assert data["gpu"]["idle_ratio"] == 0.8


def test_raises_on_no_json():
    import pytest
    with pytest.raises(ValueError):
        _parse_first_json("no json here")

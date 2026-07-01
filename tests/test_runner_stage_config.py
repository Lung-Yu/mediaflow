"""Unit tests for per-stage config injection in runner.execute()."""


def _inject(stage_dict: dict, global_cfg: dict) -> dict:
    """Replicate the injection logic from runner.execute() for isolated testing."""
    sid = stage_dict["id"]
    overrides = {k: v for k, v in stage_dict.items() if k not in ("id", "enabled")}
    if not overrides:
        return global_cfg
    return {**global_cfg, sid: {**global_cfg.get(sid, {}), **overrides}}


def test_per_stage_config_injected():
    stage = {"id": "vad_trim", "enabled": True, "aggressiveness": 3, "padding_ms": 500}
    result = _inject(stage, {})
    assert result["vad_trim"]["aggressiveness"] == 3
    assert result["vad_trim"]["padding_ms"] == 500


def test_empty_stage_config_is_noop():
    stage = {"id": "vad_trim", "enabled": True}
    cfg = {"some_key": "value"}
    result = _inject(stage, cfg)
    assert result is cfg  # no copy made


def test_global_cfg_key_not_overridden_when_empty():
    stage = {"id": "transcribe", "enabled": True}
    cfg = {"transcribe": {"service_url": "http://localhost:9001"}}
    result = _inject(stage, cfg)
    assert result["transcribe"]["service_url"] == "http://localhost:9001"


def test_per_stage_overrides_global():
    stage = {"id": "transcribe", "enabled": True, "model": "large-v3"}
    cfg = {"transcribe": {"model": "medium", "service_url": "http://localhost:9001"}}
    result = _inject(stage, cfg)
    assert result["transcribe"]["model"] == "large-v3"
    assert result["transcribe"]["service_url"] == "http://localhost:9001"  # preserved

"""Shared pipeline executor — runs configured stages in order.

Called by watcher.py (full run) and rerun.py (partial re-run).

Each stage adapter follows the protocol:
    (ctx: dict, cfg: dict) -> (ctx: dict, pub_extra: dict)

ctx is a dict carrying paths between stages:
    stem        str   — file stem (e.g. "lesson01")
    workspace   Path  — workspace root
    output_dir  Path  — workspace/3_output
    input_path  Path  — original audio (1_input/ or 4_archive/)
    audio_path  Path  — processed WAV (2_processing/)  [set by preprocess]
    srt_path    Path  — transcript (3_output/)          [set by transcribe]
    summary_md  Path  — summary markdown                [set by summarize]

pub_extra is merged into the stage.completed Redis event.
"""
import logging
import time
from pathlib import Path
from typing import Callable, Optional

from opentelemetry import metrics as _otel_metrics

from pipeline import stages
from pipeline.mq.publisher import EventPublisher

log = logging.getLogger(__name__)

_DEFAULT_STAGES = [
    {"id": "preprocess",       "enabled": True},
    {"id": "transcribe",       "enabled": True},
    {"id": "verify_segments",  "enabled": False},
    {"id": "correct_srt",      "enabled": False},
    {"id": "diarize",          "enabled": False},
    {"id": "summarize",        "enabled": True},
    {"id": "detect_chapters",  "enabled": False},
]


# ── Stage adapters ───────────────────────────────────────────────────────────

def _adapt_preprocess(ctx: dict, cfg: dict) -> tuple[dict, dict]:
    input_path = ctx.get("input_path")
    if not input_path or not input_path.exists():
        raise FileNotFoundError(
            f"No source audio for {ctx['stem']!r} — "
            "expected in 4_archive/ or 1_input/"
        )
    audio_path = stages.preprocess(input_path, ctx["workspace"], cfg)
    return {**ctx, "audio_path": audio_path}, {"filename": input_path.name}


def _adapt_transcribe(ctx: dict, cfg: dict) -> tuple[dict, dict]:
    audio_path = ctx["audio_path"]
    if not audio_path.exists():
        raise FileNotFoundError(f"Processed WAV not found: {audio_path}")
    srt_path = stages.transcribe(audio_path, ctx["stem"], ctx["output_dir"], cfg)
    return {**ctx, "srt_path": srt_path}, {"output_path": str(srt_path)}


def _adapt_verify_segments(ctx: dict, cfg: dict) -> tuple[dict, dict]:
    audio_path = ctx["audio_path"]
    srt_path = ctx["srt_path"]
    if not audio_path.exists():
        log.warning("verify_segments skipped: audio_path not found (%s)", audio_path)
        return ctx, {}
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT not found for verification: {srt_path}")
    stages.verify_segments(ctx["stem"], srt_path, audio_path, cfg)
    return ctx, {}


def _adapt_correct_srt(ctx: dict, cfg: dict) -> tuple[dict, dict]:
    srt_path = ctx["srt_path"]
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT not found for correction: {srt_path}")
    stages.correct_srt(ctx["stem"], srt_path, cfg)
    return ctx, {}


def _adapt_diarize(ctx: dict, cfg: dict) -> tuple[dict, dict]:
    srt_path = ctx["srt_path"]
    audio_path = ctx["audio_path"]
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT not found: {srt_path}")
    if not audio_path.exists():
        log.warning("diarize skipped: audio_path not found (%s)", audio_path)
        return ctx, {}
    stages.diarize(ctx["stem"], srt_path, audio_path, cfg)
    return ctx, {}


def _adapt_summarize(ctx: dict, cfg: dict) -> tuple[dict, dict]:
    srt_path = ctx["srt_path"]
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT not found: {srt_path}")
    md_path = stages.summarize(ctx["stem"], srt_path, ctx["output_dir"], cfg)
    return {**ctx, "summary_md": md_path}, {}


def _adapt_detect_chapters(ctx: dict, cfg: dict) -> tuple[dict, dict]:
    srt_path = ctx["srt_path"]
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT not found: {srt_path}")
    chapters_path = stages.detect_chapters(ctx["stem"], srt_path, ctx["output_dir"], cfg)
    return {**ctx, "chapters_path": chapters_path}, {}


STAGE_RUNNERS: dict[str, Callable] = {
    "preprocess":      _adapt_preprocess,
    "transcribe":      _adapt_transcribe,
    "verify_segments": _adapt_verify_segments,
    "correct_srt":     _adapt_correct_srt,
    "diarize":         _adapt_diarize,
    "summarize":       _adapt_summarize,
    "detect_chapters": _adapt_detect_chapters,
}

# Alias used by pipeline/worker.py
_STAGE_ADAPTERS = STAGE_RUNNERS


def _build_providers_for_stage(stage_def: dict) -> dict:
    """Build provider instances for a stage definition from stage_plan.

    Returns a dict of provider instances to be merged into the stage cfg.
    Stages that use external providers get an instance keyed by type;
    preprocess and other native stages return {}.
    """
    from pipeline.providers import (
        get_whisper_provider,
        get_llm_provider,
        get_diarize_provider,
    )
    stage_id = stage_def.get("stage", "")
    cfg = stage_def.get("config", {})
    if stage_id in ("transcribe", "verify_segments"):
        return {"whisper_provider": get_whisper_provider(cfg)}
    if stage_id in ("summarize", "correct_srt", "detect_chapters"):
        return {"llm_provider": get_llm_provider(cfg)}
    if stage_id == "diarize":
        return {"diarize_provider": get_diarize_provider(cfg)}
    return {}


# ── Executor ─────────────────────────────────────────────────────────────────

def execute(
    cfg: dict,
    ctx: dict,
    pub: EventPublisher,
    from_stage: Optional[str] = None,
    stop_after: Optional[str] = None,
) -> dict:
    """Run enabled pipeline stages in config order.

    from_stage: skip all stages before this id (used by rerun.py).
    stop_after: halt after this stage completes — later stages are skipped.
                If the named stage is disabled it never runs, so the break
                never fires and all other enabled stages run normally.
    ctx must contain: stem, workspace, output_dir.
    Pre-populate audio_path/srt_path when skipping earlier stages.
    """
    stage_cfgs = cfg.get("pipeline", {}).get("stages", _DEFAULT_STAGES)

    if from_stage:
        known_ids = [s["id"] for s in stage_cfgs]
        if from_stage not in known_ids:
            raise ValueError(
                f"Stage {from_stage!r} not found in pipeline.stages config. "
                f"Available: {known_ids}"
            )

    past_start = from_stage is None

    for s in stage_cfgs:
        sid = s["id"]
        if not past_start:
            if sid == from_stage:
                past_start = True
            else:
                continue
        if not s.get("enabled", True):
            continue
        if sid not in STAGE_RUNNERS:
            log.warning("Stage %r has no runner — add to runner.STAGE_RUNNERS", sid)
            continue
        pub.publish("stage.started", ctx["stem"], stage=sid)
        t0 = time.monotonic()
        ctx, extra = STAGE_RUNNERS[sid](ctx, cfg)
        elapsed = time.monotonic() - t0

        meter = _otel_metrics.get_meter("mediaflow.pipeline")
        meter.create_histogram(
            "mediaflow.stage.duration", unit="s",
            description="Pipeline stage processing time",
        ).record(elapsed, {"stage": sid})
        meter.create_gauge(
            "mediaflow.pipeline.last_stage_ts", unit="s",
            description="Unix timestamp of last stage completion event",
        ).set(time.time())

        pub.publish("stage.completed", ctx["stem"], stage=sid, **extra)
        if stop_after and sid == stop_after:
            log.info("stop_after_stage=%s reached, halting pipeline", stop_after)
            break

    return ctx

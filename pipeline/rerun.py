"""Re-run pipeline stages from a specific stage for a previously processed stem.

Useful when tuning Ollama prompts: skip FFmpeg + Whisper, only redo summarize().

Usage:
    python -m pipeline.rerun --stem lesson01 --from-stage summary
    python -m pipeline.rerun --stem lesson01 --from-stage transcription
    python -m pipeline.rerun --stem lesson01 --from-stage preprocessing
"""
import argparse
import logging
import sys
from pathlib import Path

from pipeline.config import load, workspace
from pipeline.mq.publisher import EventPublisher
from pipeline import stages

log = logging.getLogger(__name__)

STAGES = ["preprocessing", "transcription", "summary"]


def _find_original(stem: str, ws: Path, formats: list[str]) -> Path | None:
    for search_dir in ("4_archive", "1_input"):
        for ext in formats:
            p = ws / search_dir / f"{stem}{ext}"
            if p.exists():
                return p
    return None


def rerun(stem: str, from_stage: str, cfg: dict, pub: EventPublisher) -> None:
    ws = Path(cfg["pipeline"]["workspace_dir"])
    output_dir = ws / "3_output"
    proc_dir = ws / "2_processing"
    formats = cfg["pipeline"]["supported_formats"]

    start_idx = STAGES.index(from_stage)

    pub.publish("task.submitted", stem)

    # Stage 1 — preprocessing
    if start_idx <= 0:
        original = _find_original(stem, ws, formats)
        if not original:
            raise FileNotFoundError(
                f"No source audio for {stem!r} in 4_archive/ or 1_input/"
            )
        audio_path = stages.preprocess(original, ws, cfg)
        pub.publish("stage.completed", stem, stage="preprocessing", filename=original.name)
    else:
        audio_path = proc_dir / f"{stem}_clean.wav"

    # Stage 2 — transcription
    if start_idx <= 1:
        if not audio_path.exists():
            raise FileNotFoundError(f"Processed WAV not found: {audio_path}")
        srt_path = stages.transcribe(audio_path, stem, output_dir, cfg)
        pub.publish("stage.completed", stem, stage="transcription", output_path=str(srt_path))
    else:
        srt_path = output_dir / f"{stem}.srt"

    # Stage 3 — summary (always runs)
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT not found: {srt_path}")
    stages.summarize(stem, srt_path, output_dir, cfg)
    pub.publish("stage.completed", stem, stage="summary")

    pub.publish("task.completed", stem, output_path=str(srt_path))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-run pipeline stages for an already-processed stem"
    )
    parser.add_argument("--stem", required=True, help="File stem (e.g. lesson01)")
    parser.add_argument(
        "--from-stage",
        required=True,
        choices=STAGES,
        dest="from_stage",
        help="Stage to start from (skips earlier stages)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    cfg = load()
    pub = EventPublisher(cfg)

    log.info("Rerun %s from %s", args.stem, args.from_stage)
    try:
        rerun(args.stem, args.from_stage, cfg, pub)
        log.info("DONE %s", args.stem)
    except Exception as exc:
        log.error("FAILED %s: %s", args.stem, exc)
        pub.publish("task.failed", args.stem, error_msg=str(exc))
        sys.exit(1)


if __name__ == "__main__":
    main()

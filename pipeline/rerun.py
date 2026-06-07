"""Re-run pipeline stages from a specific stage for a previously processed stem.

Useful when tuning prompts: skip FFmpeg + Whisper, only redo summarize().

Usage:
    python -m pipeline.rerun --stem lesson01 --from-stage summary
    python -m pipeline.rerun --stem lesson01 --from-stage transcription
    python -m pipeline.rerun --stem lesson01 --from-stage preprocessing
"""
import argparse
import logging
import sys
from pathlib import Path

from pipeline.config import load
from pipeline.mq.publisher import EventPublisher
from pipeline import runner

log = logging.getLogger(__name__)


def _find_original(stem: str, ws: Path, formats: list) -> "Path | None":
    for search_dir in ("4_archive", "1_input"):
        for ext in formats:
            p = ws / search_dir / f"{stem}{ext}"
            if p.exists():
                return p
    return None


def rerun(stem: str, from_stage: str, cfg: dict, pub: EventPublisher) -> None:
    ws = Path(cfg["pipeline"]["workspace_dir"])
    formats = cfg["pipeline"]["supported_formats"]

    ctx = {
        "stem": stem,
        "input_path": _find_original(stem, ws, formats),
        "workspace": ws,
        "output_dir": ws / "3_output",
        "audio_path": ws / "2_processing" / f"{stem}_clean.wav",
        "srt_path": ws / "3_output" / f"{stem}.srt",
    }

    pub.publish("task.submitted", stem)
    ctx = runner.execute(cfg, ctx, pub, from_stage=from_stage)
    pub.publish("task.completed", stem, output_path=str(ctx["srt_path"]))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Re-run pipeline stages for an already-processed stem"
    )
    parser.add_argument("--stem", required=True, help="File stem (e.g. lesson01)")
    parser.add_argument(
        "--from-stage",
        required=True,
        dest="from_stage",
        help="Stage id to start from (skips earlier stages)",
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

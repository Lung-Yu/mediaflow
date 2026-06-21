"""Re-run pipeline stages from a specific stage for a previously processed stem.

Usage:
    python -m pipeline.rerun --stem lesson01 --from-stage summarize
"""
from __future__ import annotations
import argparse
import logging
import sys
from pathlib import Path

from pipeline.config import load
from pipeline import runner

log = logging.getLogger(__name__)


class _LogPub:
    """ponytail: minimal publisher for local rerun — just logs, no Redis."""
    def publish(self, event: str, stem: str, **kwargs):
        log.info("[%s] %s %s", event, stem, kwargs or "")

    def report_failure(self, stage: str, error_msg: str):
        log.error("[failed] stage=%s error=%s", stage, error_msg)


def _find_original(stem: str, ws: Path, formats: list) -> Path | None:
    for search_dir in ("4_archive", "1_input"):
        for ext in formats:
            p = ws / search_dir / f"{stem}{ext}"
            if p.exists():
                return p
    return None


def rerun(stem: str, from_stage: str, cfg: dict) -> None:
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
    runner.execute(cfg, ctx, _LogPub(), from_stage=from_stage)


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-run pipeline stages for an already-processed stem")
    parser.add_argument("--stem", required=True)
    parser.add_argument("--from-stage", required=True, dest="from_stage")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load()
    log.info("Rerun %s from %s", args.stem, args.from_stage)
    try:
        rerun(args.stem, args.from_stage, cfg)
        log.info("DONE %s", args.stem)
    except Exception as exc:
        log.error("FAILED %s: %s", args.stem, exc)
        sys.exit(1)


if __name__ == "__main__":
    main()

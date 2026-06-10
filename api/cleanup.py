"""Async output cleanup — expires 3_output/ stems older than configured retention."""
import asyncio
import logging
import time
from datetime import timedelta
from pathlib import Path

from api import db

log = logging.getLogger(__name__)

_OUTPUT_SUFFIXES = [
    ".srt",
    "_summary.md",
    "_summary.json",
    "_segments.json",
    "_diarization.json",
    "_speaker_names.json",
    "_chapters.json",
]


async def run_output_cleanup(output_dir: Path, retention: "timedelta | None") -> None:
    """Scan output_dir for expired stems and delete their files + DB rows."""
    if retention is None:
        return
    if not output_dir.exists():
        return

    cutoff = time.time() - retention.total_seconds()

    for srt in list(output_dir.glob("*.srt")):
        try:
            mtime = srt.stat().st_mtime
        except FileNotFoundError:
            log.debug("Already gone during scan: %s", srt.name)
            continue

        if mtime > cutoff:
            continue

        stem = srt.stem
        for suffix in _OUTPUT_SUFFIXES:
            path = output_dir / f"{stem}{suffix}"
            try:
                await asyncio.to_thread(path.unlink)
                log.info("Deleted expired output: %s", path.name)
            except FileNotFoundError:
                log.debug("Already gone: %s", path.name)
            except OSError as exc:
                log.warning("Could not delete %s: %s", path.name, exc)

        await db.delete_task(stem)
        log.info("Pruned DB row for expired stem: %s", stem)


async def cleanup_loop(output_dir: Path, retention: "timedelta | None") -> None:
    """Run output cleanup every hour. Stopped by asyncio task cancellation."""
    log.info("Output cleanup loop started (retention=%s)", retention)
    while True:
        try:
            await run_output_cleanup(output_dir, retention)
        except asyncio.CancelledError:
            log.info("Output cleanup loop stopped")
            return
        except Exception as exc:
            log.warning("Output cleanup error: %s", exc)
        await asyncio.sleep(3600)

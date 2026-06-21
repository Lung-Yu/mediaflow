"""Async output cleanup — expires 3_output/ stems older than configured retention."""
from __future__ import annotations
import asyncio
import logging
import time
from datetime import timedelta
from pathlib import Path

import asyncpg

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


async def run_output_cleanup(
    pool: asyncpg.Pool, output_dir: Path, retention: timedelta | None
) -> None:
    if retention is None:
        return
    if not output_dir.exists():
        return

    cutoff = time.time() - retention.total_seconds()

    for srt in list(output_dir.glob("*.srt")):
        try:
            mtime = srt.stat().st_mtime
        except FileNotFoundError:
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
                pass
            except OSError as exc:
                log.warning("Could not delete %s: %s", path.name, exc)

        await pool.execute("DELETE FROM events WHERE job_id = $1", stem)
        await pool.execute("DELETE FROM jobs WHERE id = $1", stem)
        log.info("Pruned DB row for expired stem: %s", stem)


async def cleanup_loop(
    pool: asyncpg.Pool, output_dir: Path, retention: timedelta | None
) -> None:
    log.info("Output cleanup loop started (retention=%s)", retention)
    while True:
        try:
            await run_output_cleanup(pool, output_dir, retention)
        except asyncio.CancelledError:
            log.info("Output cleanup loop stopped")
            return
        except Exception as exc:
            log.warning("Output cleanup error: %s", exc)
        await asyncio.sleep(3600)

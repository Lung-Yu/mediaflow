"""Queue consumer — polls pending upload tasks and downloads them to workspace.

Runs as an asyncio background task inside the API lifespan.
Every 5 s, checks if a pipeline slot is free and downloads the
oldest pending task from MinIO to workspace/1_input/ for the watcher.
"""
import asyncio
import logging
import os
from pathlib import Path

from api import db
from api import minio_client as minio_mod

log = logging.getLogger(__name__)

WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "./workspace"))
MAX_CONCURRENT = int(os.getenv("UPLOAD_MAX_CONCURRENT", "2"))


async def _tick() -> None:
    active = await db.count_active_tasks()
    if active >= MAX_CONCURRENT:
        return
    task = await db.get_oldest_pending()
    if not task:
        return

    stem = task["stem"]
    filename = task["filename"]
    minio_key = task["minio_input_key"]
    dest = WORKSPACE / "1_input" / filename

    log.info("Queue: slot free (%d/%d active), downloading %s", active, MAX_CONCURRENT, stem)
    await db.upsert_task(stem, status="downloading")
    try:
        client = minio_mod.get_client()
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, client.download_to_file, minio_key, dest)
        await db.upsert_task(stem, status="queued")
        log.info("Queue: %s ready in workspace, watcher will pick up", stem)
    except Exception as exc:
        log.error("Queue: download failed for %s: %s", stem, exc)
        await db.upsert_task(stem, status="failed", error_msg=str(exc))


async def run() -> None:
    """Run the queue consumer loop. Cancellation stops the loop cleanly."""
    log.info("Queue consumer started (max_concurrent=%d)", MAX_CONCURRENT)
    while True:
        try:
            await _tick()
        except asyncio.CancelledError:
            log.info("Queue consumer stopping")
            return
        except Exception as exc:
            log.error("Queue consumer error: %s", exc)
        await asyncio.sleep(5)

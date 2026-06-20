"""On API startup, scan workspace/3_output/ and fill any tasks missing from DB.

This ensures no permanent data loss even if Redis events were dropped
while the API was down.
"""
import os
import time
from pathlib import Path
from api import db

WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "./workspace"))


async def reconcile():
    output_dir = WORKSPACE / "3_output"
    if not output_dir.exists():
        return

    for srt in output_dir.glob("*.srt"):
        stem = srt.stem
        # Insert only if not already tracked — upsert won't overwrite existing status
        await db.upsert_task(
            stem,
            filename=f"{stem}.srt",
            status="completed",
            output_srt_path=str(srt),
            completed_at=srt.stat().st_mtime,
        )

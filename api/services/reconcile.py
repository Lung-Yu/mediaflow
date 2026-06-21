"""On API startup, scan workspace/3_output/ and fill any jobs missing from DB."""
import os
from pathlib import Path

import asyncpg

from api.db.queries import upsert_job

WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "./workspace"))


async def reconcile(pool: asyncpg.Pool) -> None:
    output_dir = WORKSPACE / "3_output"
    if not output_dir.exists():
        return
    for srt in output_dir.glob("*.srt"):
        stem = srt.stem
        await upsert_job(
            pool, stem,
            filename=f"{stem}.srt",
            status="completed",
            output_srt_path=str(srt),
            completed_at=srt.stat().st_mtime,
        )

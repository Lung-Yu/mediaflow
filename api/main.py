import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import db
from api import minio_client as minio_mod
from api import cleanup
from api.lifecycle import parse_retention
from api.reconcile import reconcile
from api.mq import consumer
from api.mq import queue_consumer
from api.routes import events, files, stats, status, tasks, upload

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mediaflow:changeme@localhost:5432/mediaflow",
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    await db.init(app.state.pool)
    await reconcile()

    minio_mod.init_client()
    try:
        minio_mod.get_client().ensure_buckets()
    except Exception as exc:
        logging.getLogger(__name__).warning("MinIO unavailable on startup: %s", exc)

    # Set MinIO bucket lifecycle rules from env vars
    for bucket, env_key in [
        (minio_mod.INPUT_BUCKET, "LIFECYCLE_MINIO_INPUT"),
        (minio_mod.OUTPUT_BUCKET, "LIFECYCLE_MINIO_OUTPUT"),
    ]:
        days_str = os.getenv(env_key, "forever")
        ret = parse_retention(days_str)
        if ret is not None and ret.total_seconds() > 0:
            try:
                minio_mod.get_client().set_bucket_lifecycle(bucket, int(ret.total_seconds() // 86400))
            except Exception as exc:
                logging.getLogger(__name__).warning(
                    "Could not set MinIO lifecycle for %s: %s", bucket, exc
                )

    # Start output cleanup loop
    output_retention = parse_retention(os.getenv("LIFECYCLE_OUTPUT", "forever"))
    output_dir = Path(os.getenv("WORKSPACE_DIR", "./workspace")) / "3_output"
    cleanup_task = asyncio.create_task(cleanup.cleanup_loop(output_dir, output_retention))

    redis_task = asyncio.create_task(consumer.run())
    queue_task = asyncio.create_task(queue_consumer.run())
    yield
    for task in [cleanup_task, redis_task, queue_task]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await app.state.pool.close()


app = FastAPI(title="mediaflow API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(events.router)
app.include_router(files.router)
app.include_router(stats.router)
app.include_router(status.router)
app.include_router(tasks.router)
app.include_router(upload.router)


@app.get("/health")
def health():
    return {"status": "ok"}

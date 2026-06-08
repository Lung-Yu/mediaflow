import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api import db
from api import minio_client as minio_mod
from api.reconcile import reconcile
from api.mq import consumer
from api.mq import queue_consumer
from api.routes import events, files, status, upload


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    await reconcile()

    minio_mod.init_client()
    try:
        minio_mod.get_client().ensure_buckets()
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("MinIO unavailable on startup: %s", exc)

    redis_task = asyncio.create_task(consumer.run())
    queue_task = asyncio.create_task(queue_consumer.run())
    yield
    for task in [redis_task, queue_task]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="mediaflow API", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(events.router)
app.include_router(files.router)
app.include_router(status.router)
app.include_router(upload.router)


@app.get("/health")
def health():
    return {"status": "ok"}

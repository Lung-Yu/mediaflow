import asyncio
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry import metrics as _otel_metrics
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

from api import db
from api.utils import minio as minio_mod
from api.utils import cleanup
from api.utils.lifecycle import parse_retention
from api.services.reconcile import reconcile
from api.mq import events_consumer
from api.mq import jobs_consumer
from api.routes import events, files, jobs as jobs_router, stats, status, tasks, upload
from api.routes import dag_callback, correction

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mediaflow:changeme@localhost:5432/mediaflow",
)
REDIS_URL = "redis://{}:{}".format(
    os.getenv("REDIS_HOST", "localhost"),
    os.getenv("REDIS_PORT", "6379"),
)


def _init_otel() -> None:
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "otel-collector:4317")
    exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
    reader = PeriodicExportingMetricReader(exporter, export_interval_millis=15_000)
    provider = MeterProvider(metric_readers=[reader])
    _otel_metrics.set_meter_provider(provider)


_init_otel()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=10)
    app.state.redis = aioredis.from_url(REDIS_URL, decode_responses=True)
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

    redis_task = asyncio.create_task(events_consumer.run())
    queue_task = asyncio.create_task(jobs_consumer.run())
    yield
    for task in [cleanup_task, redis_task, queue_task]:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await app.state.pool.close()
    await app.state.redis.aclose()


app = FastAPI(title="mediaflow API", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

app.include_router(events.router)
app.include_router(files.router)
app.include_router(jobs_router.router)
app.include_router(stats.router)
app.include_router(status.router)
app.include_router(tasks.router)
app.include_router(upload.router)
app.include_router(dag_callback.router)
app.include_router(correction.router)


@app.get("/health")
def health():
    return {"status": "ok"}

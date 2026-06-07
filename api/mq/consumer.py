"""Redis Streams consumer — bridges pipeline events into the API DB.

Runs as a background asyncio task inside the API lifespan.
The pipeline watcher publishes to the stream; this consumer reads and
calls process_event() so DB state stays consistent even if the HTTP
/events/stage-complete endpoint is never called directly.
"""
import asyncio
import logging
import os
import redis.asyncio as aioredis
from api.event_processor import process_event

log = logging.getLogger(__name__)

REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", 6379))
STREAM_KEY = "mediaflow:events"
GROUP = "api-consumers"
CONSUMER = "api-1"


async def _drain_pending(r: aioredis.Redis) -> None:
    """Replay messages delivered but not ACKed before this consumer started."""
    while True:
        results = await r.xreadgroup(GROUP, CONSUMER, {STREAM_KEY: "0"}, count=100)
        if not results:
            break
        entries = results[0][1]
        if not entries:
            break
        for msg_id, fields in entries:
            try:
                await process_event(fields)
                await r.xack(STREAM_KEY, GROUP, msg_id)
            except Exception as exc:
                log.error("Pending msg %s failed: %s", msg_id, exc)


async def run() -> None:
    r = aioredis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)

    # Create consumer group idempotently; start from "0" to replay history.
    try:
        await r.xgroup_create(STREAM_KEY, GROUP, id="0", mkstream=True)
    except Exception:
        pass  # group already exists

    await _drain_pending(r)
    log.info("Redis consumer ready — stream=%s group=%s", STREAM_KEY, GROUP)

    while True:
        try:
            results = await r.xreadgroup(
                GROUP, CONSUMER, {STREAM_KEY: ">"}, count=10, block=2000
            )
            if results:
                for _, entries in results:
                    for msg_id, fields in entries:
                        try:
                            await process_event(fields)
                            await r.xack(STREAM_KEY, GROUP, msg_id)
                        except Exception as exc:
                            log.error("Msg %s failed: %s", msg_id, exc)
        except asyncio.CancelledError:
            log.info("Redis consumer stopping")
            await r.aclose()
            return
        except Exception as exc:
            log.error("Consumer loop error: %s — retrying in 5s", exc)
            await asyncio.sleep(5)

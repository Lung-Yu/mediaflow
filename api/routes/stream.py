"""Server-sent events — pushes pipeline status to the browser every 5 s."""
import asyncio
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from api.db.queries import get_status_overview

router = APIRouter(prefix="/events")
log = logging.getLogger(__name__)


@router.get("/stream")
async def sse_stream(request: Request):
    pool = request.app.state.pool

    async def generator():
        yield "retry: 5000\n\n"
        while not await request.is_disconnected():
            try:
                data = await get_status_overview(pool)
                payload = json.dumps(data)
                yield f"event: status\ndata: {payload}\n\n"
            except Exception as exc:
                log.warning("SSE error: %s", exc)
            await asyncio.sleep(5)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )

"""Fire-and-forget webhook on task completion or failure."""
import logging
import os
import httpx

log = logging.getLogger(__name__)

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")


async def notify(payload: dict) -> None:
    """POST payload to WEBHOOK_URL. Silently swallows errors — notifications are best-effort."""
    if not WEBHOOK_URL:
        return
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.post(WEBHOOK_URL, json=payload)
            log.info("Webhook delivered: %s → HTTP %s", WEBHOOK_URL, r.status_code)
    except Exception as exc:
        log.warning("Webhook delivery failed: %s", exc)

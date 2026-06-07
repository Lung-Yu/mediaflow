"""Redis Streams publisher — pipeline side."""
import redis
import json
import time


class EventPublisher:
    def __init__(self, cfg: dict):
        r_cfg = cfg["redis"]
        self._r = redis.Redis(host=r_cfg["host"], port=r_cfg["port"], decode_responses=True)
        self._stream = r_cfg["stream_key"]

    def publish(self, event_type: str, stem: str, **kwargs):
        payload = {
            "event": event_type,
            "stem": stem,
            "ts": time.time(),
            **{k: str(v) for k, v in kwargs.items()},
        }
        self._r.xadd(self._stream, payload)

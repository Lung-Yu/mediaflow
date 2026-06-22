# MediaFlow — Mitigation Measures

**Date**: 2026-06-23

---

## P0 — Must Fix Before Network Exposure (~7 hours total)

### M1: Rotate All Default Credentials
**VR**: 001, 008 | **Effort**: 30 min

1. Create `.env` in project root (already gitignored via `.gitignore` convention):
   ```
   POSTGRES_PASSWORD=<strong-random>
   MINIO_ACCESS_KEY=<strong-random>
   MINIO_SECRET_KEY=<strong-random>
   GRAFANA_PASSWORD=<strong-random>
   REDIS_PASSWORD=<strong-random>
   API_KEY=<strong-random>
   WORKER_CALLBACK_SECRET=<strong-random>
   ```
2. Reference in `docker-compose.yml` via `${VAR}` (already done for most vars).
3. Update `config.yaml.example` to note that these must be set before first run.

---

### M2: Redis Authentication
**VR**: 003 | **Effort**: 15 min

`docker-compose.yml`:
```yaml
redis:
  command: >
    redis-server --appendonly yes --appendfsync always
    --requirepass ${REDIS_PASSWORD:-changeme}
```

`api/main.py`:
```python
REDIS_URL = "redis://:{}@{}:{}".format(
    os.getenv("REDIS_PASSWORD", ""),
    os.getenv("REDIS_HOST", "localhost"),
    os.getenv("REDIS_PORT", "6379"),
)
```

`pipeline/worker.py` — update `redis.Redis(...)` call to include `password=os.getenv("REDIS_PASSWORD")`.

---

### M3: Shared Secret for Worker→DAG Callback
**VR**: 002 | **Effort**: 2 hours

**`api/routes/dag_callback.py`**:
```python
import os
from fastapi import Header, HTTPException

_SECRET = os.getenv("WORKER_CALLBACK_SECRET", "")

@router.post("/internal/stage-callback", status_code=204)
async def stage_callback(req: StageCallbackRequest, request: Request,
                         x_worker_secret: str = Header(default="")):
    if _SECRET and x_worker_secret != _SECRET:
        raise HTTPException(403, "Forbidden")
    ...
```

**`pipeline/worker.py` `_CallbackPub._post()`**:
```python
import os
_SECRET = os.getenv("WORKER_CALLBACK_SECRET", "")

def _post(self, status, stage, error_msg):
    headers = {}
    if _SECRET:
        headers["X-Worker-Secret"] = _SECRET
    httpx.post(..., headers=headers)
```

---

### M4: Static API Key Authentication
**VR**: 004, 005, 006, 007, 010, 013 | **Effort**: 4 hours

**New file `api/deps.py`**:
```python
import os
from fastapi import HTTPException, Security
from fastapi.security import APIKeyHeader

_KEY = os.getenv("API_KEY", "")
_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

def require_key(key: str = Security(_scheme)):
    if _KEY and key != _KEY:
        raise HTTPException(401, "Invalid or missing API key")
```

Apply to all routers in `api/main.py`:
```python
from api.deps import require_key
app.include_router(jobs_router.router, dependencies=[Depends(require_key)])
app.include_router(upload.router, dependencies=[Depends(require_key)])
# etc.
```

Exempt: `GET /health`, `GET /status` (monitoring).

**Frontend**: store `API_KEY` in `VITE_API_KEY` env var; include as `X-Api-Key` header in `client.ts`.

---

## P1 — Short Term (~10 hours total)

### M5: Rate Limiting on Write Endpoints
**VR**: 005, 009 | **Effort**: 2 hours

Add `slowapi` to `requirements.txt`. Apply `@limiter.limit("10/minute")` to:
- `POST /jobs`
- `POST /upload/init`
- `POST /upload/complete`
- `POST /jobs/{id}/rerun`

---

### M6: Restrict MinIO CORS to Frontend Origin
**VR**: 011 | **Effort**: 15 min

`docker-compose.yml`:
```yaml
MINIO_API_CORS_ALLOW_ORIGIN: "${FRONTEND_ORIGIN:-http://localhost:3000}"
```

---

### M7: File Magic Byte Validation
**VR**: 014 | **Effort**: 3 hours

In `api/services/project.py` after `head_object`, before `validate_fr6`:
```python
import magic  # python-magic

ALLOWED = {"audio/", "video/"}

raw = minio.get_bytes(file_key, bucket=minio.input_bucket, max_bytes=8192)
mime = magic.from_buffer(raw, mime=True)
if not any(mime.startswith(p) for p in ALLOWED):
    raise HTTPException(400, f"Unsupported file type")
```

Add `python-magic` to `requirements.txt`.

---

### M8: Cap `initial_prompt` Length
**VR**: 021 | **Effort**: 30 min

`api/routes/upload.py`:
```python
from pydantic import Field

class CompleteRequest(BaseModel):
    ...
    initial_prompt: str = Field("", max_length=500)
```

---

### M9: Sanitize Error Responses
**VR**: 016 | **Effort**: 2 hours

Replace exception details in `HTTPException` messages with generic strings. Log full exception via `log.exception(...)` server-side only.

Example (`api/services/project.py`):
```python
except Exception:
    log.exception("MinIO head_object failed for key %s", file_key)
    raise HTTPException(400, "File not found in storage")
```

---

## P2 — Medium Term (~9 hours total)

### M10: Bind Service Ports to Localhost
**VR**: 001, 003, 008 (defense in depth) | **Effort**: 30 min

`docker-compose.yml` — for all non-public services:
```yaml
ports:
  - "127.0.0.1:6379:6379"
  - "127.0.0.1:9000:9000"
  - "127.0.0.1:9002:9002"
  - "127.0.0.1:3001:3001"
  - "127.0.0.1:5432:5432"
```

Only `8080` (API) and `3000` (frontend) should be publicly accessible.

---

### M11: Soft-Delete Jobs (Preserve Audit Trail)
**VR**: 013 | **Effort**: 2 hours

Add migration: `ALTER TABLE jobs ADD COLUMN deleted_at REAL DEFAULT NULL`.

`DELETE /jobs/{id}` → sets `deleted_at = now()` instead of hard delete.

`GET /jobs` → adds `WHERE deleted_at IS NULL`.

Add separate `POST /jobs/{id}/purge` (admin-only) for hard delete.

---

### M12: Webhook SSRF Prevention
**VR**: 015 | **Effort**: 2 hours

Validate `WEBHOOK_URL` on startup:
```python
import ipaddress, urllib.parse

def _is_safe_url(url: str) -> bool:
    p = urllib.parse.urlparse(url)
    try:
        addr = ipaddress.ip_address(p.hostname)
        return addr.is_global and not addr.is_private
    except ValueError:
        return True  # hostname, not IP — allow (DNS rebinding is out of scope)
```

Also: `follow_redirects=False` already missing from httpx client — add it.

---

### M13: Stage Allowlist in Callback Handler
**VR**: T4 | **Effort**: 1 hour

`api/routes/dag_callback.py`:
```python
VALID_STAGES = {"preprocess", "segment_audio", "transcribe", "verify_segments",
                "correct_srt", "diarize", "summarize", "detect_chapters"}

if req.stage not in VALID_STAGES:
    raise HTTPException(400, f"Unknown stage")
```

---

### M14: Prompt Injection Hardening
**VR**: 012 | **Effort**: 4 hours

`pipeline/prompts.py` — wrap transcript content in delimiters:
```
<transcript>
{transcript_text}
</transcript>
```

Add post-processing validation: parse LLM JSON output with `json.loads()` and validate against expected schema before storing. If parsing fails, flag job for manual review rather than silently accepting malformed output.

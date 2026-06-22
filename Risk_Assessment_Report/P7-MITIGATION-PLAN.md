# P7 — Mitigation Plan

Priority tiers: **P0** = fix before any network exposure | **P1** = fix within 1 sprint | **P2** = fix within 1 month

---

## P0 — Immediate (Block Network Exposure)

### M1: Rotate all default credentials
**Addresses**: VR-001, VR-008  
**Effort**: 30 min  
- `docker-compose.yml`: set `POSTGRES_PASSWORD`, `MINIO_SECRET_KEY`, `MINIO_ACCESS_KEY`, `GRAFANA_PASSWORD` to strong random values
- Move secrets to `.env` file (already gitignored pattern in place)
- Document in `config.yaml.example` that these must be changed

### M2: Add Redis password
**Addresses**: VR-003  
**Effort**: 15 min  
```yaml
# docker-compose.yml
redis:
  command: >
    redis-server
    --appendonly yes
    --appendfsync always
    --requirepass ${REDIS_PASSWORD:-changeme}
```
Worker and API must pass `redis://:password@host:port`.

### M3: Isolate `/internal/stage-callback` from public port
**Addresses**: VR-002, S1, T2  
**Effort**: 2 hours  
Two options (either works):
- **Option A (simplest)**: Add a shared secret env var `WORKER_CALLBACK_SECRET`; worker sends it as `X-Worker-Secret` header; API validates it in the route handler
- **Option B (network)**: Bind a second Uvicorn on port 8081 (internal only) for `/internal/*` routes; block port 8081 from docker external exposure

Option A recommended (less infra change):
```python
# dag_callback.py
from fastapi import Header, HTTPException
import os

_SECRET = os.getenv("WORKER_CALLBACK_SECRET", "")

@router.post("/internal/stage-callback", status_code=204)
async def stage_callback(req: StageCallbackRequest, request: Request,
                         x_worker_secret: str = Header(default="")):
    if _SECRET and x_worker_secret != _SECRET:
        raise HTTPException(403, "Forbidden")
    ...
```
Worker sets header in `_CallbackPub._post()`.

### M4: Add API key authentication
**Addresses**: VR-004, VR-005, VR-006, VR-007, VR-010, VR-013  
**Effort**: 4 hours  
Single-user → one static API key in env var is sufficient. No need for sessions or JWT.
```python
# api/deps.py
import os
from fastapi import Header, HTTPException, Security
from fastapi.security import APIKeyHeader

_KEY = os.getenv("API_KEY", "")
_scheme = APIKeyHeader(name="X-API-Key", auto_error=False)

def require_key(key: str = Security(_scheme)):
    if _KEY and key != _KEY:
        raise HTTPException(401, "Invalid API key")
```
Apply as dependency to all routers except health check. Frontend stores key in localStorage or env.

---

## P1 — Short Term (Within 1 Sprint)

### M5: Add rate limiting on write endpoints
**Addresses**: VR-005, VR-009  
**Effort**: 2 hours  
```python
# requirements.txt: slowapi
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

@router.post("")
@limiter.limit("10/minute")
async def create_job(...): ...
```
Also add `max_file_count_per_hour` guard in Project Service.

### M6: Restrict MinIO CORS to frontend origin
**Addresses**: VR-011  
**Effort**: 15 min  
```yaml
# docker-compose.yml
MINIO_API_CORS_ALLOW_ORIGIN: "http://localhost:3000"
```
In production, set to the actual frontend domain.

### M7: Validate file content type (magic bytes)
**Addresses**: VR-014  
**Effort**: 3 hours  
Add a validation stage before Project Service triggers DAG: use `python-magic` to inspect the first 8KB of the uploaded file against an allowlist of audio/video MIME types.
```python
ALLOWED_MIME_PREFIXES = ["audio/", "video/"]
```
Reject and clean up if mismatch.

### M8: Validate `initial_prompt` length and content
**Addresses**: VR-021  
**Effort**: 30 min  
```python
# upload.py InitRequest
initial_prompt: str = Field("", max_length=500)
```

### M9: Sanitize error messages in API responses
**Addresses**: VR-016  
**Effort**: 2 hours  
Replace `raise HTTPException(400, f"File not found in storage: {exc}")` with generic messages. Log full exception server-side only.

### M10: Add upload size rate limiting per-session
**Addresses**: VR-009  
**Effort**: 3 hours  
Track cumulative uploaded bytes per source IP in Redis with 1-hour TTL window.

---

## P2 — Medium Term (Within 1 Month)

### M11: Soft-delete jobs (preserve audit trail)
**Addresses**: VR-013  
**Effort**: 2 hours  
Add `deleted_at` column to `jobs`; `DELETE /jobs/{id}` sets `deleted_at` and hides from `GET /jobs` but preserves events. Add a separate admin purge endpoint.

### M12: Webhook URL allowlist / SSRF prevention
**Addresses**: VR-015  
**Effort**: 2 hours  
Validate `WEBHOOK_URL` on startup against an allowlist or check that the host is not RFC-1918 private. Use `httpx` with `follow_redirects=False` and timeout already set correctly.

### M13: Bind Docker ports to localhost only
**Addresses**: VR-001, VR-003, VR-008 (defense in depth)  
**Effort**: 30 min  
```yaml
# docker-compose.yml — for all non-API services
ports:
  - "127.0.0.1:6379:6379"  # Redis
  - "127.0.0.1:9000:9000"  # MinIO API
  - "127.0.0.1:9002:9002"  # MinIO console
  - "127.0.0.1:3001:3001"  # Grafana
  - "127.0.0.1:5432:5432"  # PostgreSQL
```
Only expose port 8080 (API) and 3000 (frontend) externally.

### M14: Prompt injection hardening
**Addresses**: VR-012  
**Effort**: 4 hours  
- Wrap transcript in XML/delimiter tags in all Ollama prompts to prevent instruction injection
- Validate LLM output format (JSON schema check) before storing; reject / flag if schema mismatch
- Document in `pipeline/prompts.py` that transcript is untrusted input

### M15: Add `stage` allowlist in stage-callback handler
**Addresses**: T4 (data quality)  
**Effort**: 1 hour  
```python
VALID_STAGES = {"preprocess", "segment_audio", "transcribe", "verify_segments",
                "correct_srt", "diarize", "summarize", "detect_chapters"}
if req.stage not in VALID_STAGES:
    raise HTTPException(400, f"Unknown stage: {req.stage!r}")
```

---

## Mitigation Priority Matrix

| Risk | M-IDs | Priority | Estimated Effort |
|---|---|---|---|
| Default credentials everywhere | M1 | **P0** | 30 min |
| Redis unauthenticated | M2 | **P0** | 15 min |
| Internal callback exposed | M3 | **P0** | 2h |
| No API auth | M4 | **P0** | 4h |
| Queue flooding | M5, M10 | P1 | 5h |
| MinIO CORS | M6 | P1 | 15 min |
| File content validation | M7 | P1 | 3h |
| Error message leakage | M9 | P1 | 2h |
| Audit trail destruction | M11 | P2 | 2h |
| SSRF webhook | M12 | P2 | 2h |
| Port binding to localhost | M13 | P2 | 30 min |
| LLM prompt injection | M14 | P2 | 4h |

**Total P0 effort: ~7 hours**  
**Total P1 effort: ~10 hours**  
**Total P2 effort: ~9 hours**

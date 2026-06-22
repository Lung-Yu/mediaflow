# P3 — Trust Boundary Analysis

---

## Trust Zones

| Zone | Components | Trust Level |
|---|---|---|
| Z1: Public Internet | Browser, external webhook recipients | Untrusted |
| Z2: Docker Network | API, PostgreSQL, Redis, MinIO, monitoring | Semi-trusted (Docker isolation) |
| Z3: Host (Apple Silicon) | Worker, Watcher, Whisper, Ollama | Trusted (local processes) |
| Z4: Storage Plane | MinIO buckets, PostgreSQL data | Data plane — trust inherited from accessor |

---

## Trust Boundaries

### TB1: Internet ↔ FastAPI (port 8080)
- **Crossing**: All API requests from browser or external tools
- **Controls present**: None — no authentication, no authorization, no rate limiting, no IP allowlist
- **Risk**: Any internet-reachable host can call any API endpoint with full capability

### TB2: Internet ↔ MinIO (port 9000, presigned)
- **Crossing**: Browser direct file upload/download via presigned URLs
- **Controls present**: Time-limited presigned URLs (upload_id tied to multipart upload)
- **Risk**: Presigned PUT URLs allow uploading arbitrary content directly to MinIO bypassing API content inspection. CORS `*` means any webpage can trigger uploads using a leaked URL.

### TB3: FastAPI ↔ Redis (port 6379)
- **Crossing**: XADD from DAG-Service; XREADGROUP from Worker
- **Controls present**: None — Redis has no password in docker-compose
- **Risk**: Any process reaching port 6379 can inject or read job messages

### TB4: Worker (host) ↔ FastAPI /internal/stage-callback (port 8080)
- **Crossing**: Worker POSTs stage results after each pipeline step
- **Controls present**: None — /internal/ is a URL prefix, not network isolation
- **Risk**: `/internal/stage-callback` is reachable from the same public port 8080 as user-facing APIs; any caller can forge stage results for any job_id

### TB5: FastAPI ↔ MinIO (port 9000, internal)
- **Crossing**: S3 API calls for head_object, copy, presign generation
- **Controls present**: Access key + secret key (defaults: `mediaflow` / `changeme`)
- **Risk**: Weak defaults. No bucket-level policies differentiating read vs. write per caller.

### TB6: FastAPI ↔ PostgreSQL (port 5432)
- **Crossing**: asyncpg connection pool
- **Controls present**: Username + password (`changeme` default)
- **Risk**: DB credential in plain env var; no TLS; default password.

### TB7: Worker ↔ Whisper (port 9001) / Ollama (port 11434)
- **Crossing**: HTTP POST with audio bytes or transcript text
- **Controls present**: None
- **Risk**: Both services are unauthenticated. Prompt injection into Ollama via crafted transcript text or initial_prompt.

### TB8: Filesystem ↔ Watcher (workspace/1_input/)
- **Crossing**: Files dropped into watched directory
- **Controls present**: FR6 check (filename, size only) — applied *after* file is already on filesystem
- **Risk**: Arbitrary files reach the host filesystem before any validation. Symlink attacks possible.

---

## Critical Boundary Violations

| Violation | Description | Severity |
|---|---|---|
| V1 | `/internal/stage-callback` exposed on public port | **Critical** |
| V2 | Redis unauthenticated, port 6379 accessible | **High** |
| V3 | No auth on any API endpoint | **High** |
| V4 | MinIO CORS `*` + default credentials | **High** |
| V5 | Worker POSTs to public API without any shared secret | **High** |
| V6 | Grafana port 3001 with default `admin` password | **Medium** |
| V7 | MinIO console port 9002 with default credentials | **Medium** |
| V8 | All inter-service traffic unencrypted (HTTP) | **Medium** |

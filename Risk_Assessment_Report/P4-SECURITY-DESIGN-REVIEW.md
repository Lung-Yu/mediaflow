# P4 — Security Design Review

---

## Authentication & Authorization

| Control | Status | Finding |
|---|---|---|
| API authentication | **Missing** | Zero auth on all 13 entry points |
| Authorization / RBAC | **Missing** | No concept of ownership; any caller can delete/rerun/correct any job |
| Internal endpoint isolation | **Missing** | `/internal/stage-callback` reachable from public internet via same port 8080 |
| Redis authentication | **Missing** | `docker-compose.yml` has no `requirepass` directive |
| MinIO auth | Present (default creds) | `changeme` / `mediaflow` are shipped defaults — must be rotated before network exposure |
| Grafana auth | Present (default creds) | `admin` default must be rotated |
| Worker→API shared secret | **Missing** | Worker has no credential when calling back to `/internal/stage-callback` |

---

## Input Validation

| Control | Status | Finding |
|---|---|---|
| FR6 filename check | Partial | Checks null byte, `..`, `/` prefix, length, size. Does NOT check MIME type or file magic bytes. |
| Content-type validation | **Missing** | `upload/init` accepts any `content_type`; no server-side MIME validation |
| File content inspection | **Missing** | Files go directly from browser to MinIO; API never reads the bytes |
| `initial_prompt` validation | **Missing** | Passed verbatim to Whisper; no length cap or character filtering (`upload/complete:req.initial_prompt`) |
| `stage` field in callback | **Missing** | Any string accepted as stage name; injected into events table raw |
| `dag_flow` in POST /jobs | Minimal | Looked up from DB; invalid values return 404, but caller controls which flow runs |
| Segment index in clip route | Minimal | Integer path param, but `segments.json` content is trusted from MinIO without re-validation |
| Correction segments array | Minimal | Accepts `start`/`end` as arbitrary floats — no bounds vs. total file duration |

---

## Injection Risks

| Type | Location | Detail |
|---|---|---|
| Prompt injection | `pipeline/stages.py` (summarize, correct_srt) | Transcript text → Ollama prompt. Malicious audio could produce transcript designed to hijack LLM output format or exfiltrate data through summary. |
| Command injection (FFmpeg) | `api/routes/clip.py:_extract_clip` | `start`/`end` values from `segments.json` passed as CLI args via `subprocess.run`. If segments.json is tampered in MinIO, an attacker could inject shell metacharacters. However, `str(start)` and `str(end)` are float-cast first, which prevents injection. Low risk as-is. |
| SQL injection | `api/db/queries.py` | Uses asyncpg parameterized queries throughout — no raw string interpolation found. **Safe.** |
| Path traversal | `api/routes/clip.py:_build_clip` | `clip_key` built from `job_id` + `index`, both controlled by API logic. `job_id` constructed from UUID + filename stem. Safe as-is, but `minio_processing_key` from DB is passed directly to download — if DB is compromised, arbitrary MinIO key read. |

---

## Secrets Management

| Secret | Storage | Default | Risk |
|---|---|---|---|
| PostgreSQL password | `docker-compose.yml` env var | `changeme` | High — committed default |
| MinIO access key | `docker-compose.yml` env var | `mediaflow` | High |
| MinIO secret key | `docker-compose.yml` env var | `changeme` | High |
| Grafana password | `docker-compose.yml` env var | `admin` | Medium |
| `config.yaml` (Whisper/Ollama URLs, etc.) | Gitignored | N/A | Low — gitignored correctly |
| `WEBHOOK_URL` | env var | Empty | Medium — external POST target |

---

## Missing Security Controls Summary

| Control | Priority | Notes |
|---|---|---|
| Auth on API (even basic bearer token) | **Critical** | Single-user → API key sufficient |
| Network isolation for `/internal/` routes | **Critical** | Bind to localhost or separate port |
| Redis `requirepass` | **High** | One-line addition to docker-compose |
| Rotate all default credentials | **High** | changeme/admin/mediaflow |
| Rate limiting on POST /jobs | **High** | asyncio-based or nginx level |
| Worker→DAG shared secret header | **High** | X-Worker-Secret header check |
| File content validation (magic bytes) | **Medium** | python-magic or ffprobe before pipeline |
| MinIO CORS restriction | **Medium** | Restrict to frontend origin only |
| `initial_prompt` length cap | **Low** | Cap at ~500 chars in upload validation |

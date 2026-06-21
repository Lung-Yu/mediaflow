# mediaflow — Claude Handoff Guide

Audio recording pipeline that converts recordings into transcripts and structured summaries.
Built for a Mac mini running Apple Silicon + Docker.

> **Design details** (API, DB schema, DAG flows, MinIO buckets, concurrency model):
> see [`docs/architecture.md`](docs/architecture.md)

---

## System Architecture

```
[Host — native, Apple Silicon GPU-bound]
  pipeline/watcher.py
    ├── watches workspace/1_input/ (watchdog, PollingObserver)
    ├── uploads each file to MinIO input/{uuid}_{filename}
    └── POST /jobs → same flow as front-end upload

  pipeline/worker.py  (separate long-running process)
    ├── XREADGROUP mediaflow:jobs (Redis stream)
    ├── downloads audio from MinIO processing/
    ├── runs FFmpeg → Whisper → Ollama stages (ThreadPoolExecutor)
    ├── uploads outputs + intermediates to MinIO
    └── POST /internal/stage-callback → DAG-Service after each stage

  External services (must run before worker starts):
    Whisper HTTP  localhost:9001   (mlx-community/whisper-medium-mlx via ctl.sh)
    Ollama        localhost:11434  (model configured in config.yaml)

                    ↕ Redis Stream (mediaflow:jobs)

[Docker Compose — api + postgres + redis + minio + monitoring]
  postgres  port 5432   — primary DB (jobs, events, dag_flows tables)
  redis     port 6379   — MQ for pipeline jobs
  minio     port 9000   — object storage (input / processing / output / clips buckets)
  api       port 8080   — FastAPI: REST + DAG-Service + watchdog
  grafana   port 3001   — metrics (Prometheus + OTel)

[Frontend]
  frontend/  — React + Vite (port 3000 via dev server or nginx)
```

**Job flow:**
```
upload → Project Service → DAG-Service → xadd mediaflow:jobs
Worker: xack immediately → stages → POST /internal/stage-callback per stage
DAG-Service: on failure → retry with backoff (up to PIPELINE_MAX_RETRIES=3)
Watchdog: every 5min, re-enqueue jobs stuck > PIPELINE_JOB_TIMEOUT_SEC (1h)
```

**Why host-side worker**: Whisper (mlx-whisper) and Ollama use Apple Silicon GPU, cannot run in Docker.

---

## Workspace Layout

```
workspace/
  1_input/       ← drop files here; watcher picks them up
  2_processing/  ← FFmpeg WAV intermediates (legacy local mode only)
  3_output/      ← final SRT, _summary.md, _summary.json (legacy local mode only)
  4_archive/     ← original files after pipeline (legacy local mode only)

models/          ← ML model files (gitignored); download via scripts/download-models.sh
```

> In v2 (current), all pipeline I/O goes through MinIO. The workspace dirs are used by
> legacy `rerun.py` and local development only.

---

## Key Files

```
pipeline/
  watcher.py          — watchdog loop; uploads to MinIO; POSTs to /jobs
  worker.py           — MQ consumer; runs stages; POSTs stage callbacks; uploads outputs
  runner.py           — stage executor (STAGE_RUNNERS registry, per_stage_done hook)
  stages.py           — all stage functions: preprocess / transcribe / summarize / etc.
  rerun.py            — CLI: --stem / --from-stage local re-run (dev tool)
  config.py           — load config.yaml + workspace path helper
  providers/          — WhisperProvider, LLMProvider, DiarizeProvider abstractions

api/
  main.py             — FastAPI lifespan: DB pool, Redis, MinIO, watchdog, cleanup loop
  db/
    queries.py        — asyncpg query functions (upsert_job, get_job, insert_event, …)
    migrations/       — SQL migration files (applied on startup)
  services/
    dag.py            — DAG-Service: trigger_job, handle_stage_callback, recover_stuck_jobs
    project.py        — Project Service: on_upload_trigger (FR6, copy to processing/, create job)
    correction.py     — FR4: rebuild_srt, apply_correction, finalize_correction
    reconcile.py      — on startup: scan 3_output/*.srt and fill DB gaps
    webhook.py        — fire-and-forget POST on task.completed / task.failed
  routes/
    jobs.py           — GET/POST/DELETE /jobs, GET /jobs/{id}/events, POST /jobs/{id}/rerun
    upload.py         — POST /upload/init, /upload/complete (presigned multipart)
    files.py          — GET /files/, /files/{stem}/srt, /files/{stem}/audio, etc.
    dag_callback.py   — POST /internal/stage-callback
    correction.py     — PATCH /jobs/{id}/correction, POST /jobs/{id}/correction/finalize
    clip.py           — GET /jobs/{id}/segment/{index}/audio (on-demand MinIO clip)
    status.py         — GET /status/ (dashboard data)
    stats.py          — GET /stats/ (analytics)
  utils/
    minio.py          — MinIOClient wrapper (boto3); buckets: input/processing/output/clips
    cleanup.py        — async output/ expiry loop
    lifecycle.py      — retention string parser

frontend/             — React + Vite SPA
  src/api/client.ts   — typed API client (all /api/* calls)
  src/api/types.ts    — shared TypeScript types

diarize/
  service.py          — FastAPI diarization on :9003; speechbrain ECAPA-TDNN (optional)

scripts/
  ctl.sh              — unified service control
  download-models.sh  — download RNNoise model files

docs/
  architecture.md     — full design: API, DB schema, DAG flows, MinIO buckets, retry model
```

---

## Configuration (`config.yaml`)

Copy from `config.yaml.example`. Key sections:

```yaml
pipeline:
  workspace_dir: ./workspace
  max_concurrent_jobs: 2      # worker thread pool + DAG-Service cap
  max_retries: 3
  retry_backoff_sec: 30

whisper:
  service_url: http://localhost:9001
  language: zh
  model: medium               # or large-v3 for verify_segments

ollama:
  service_url: http://localhost:11434
  model: qwen2.5:14b

redis:
  host: localhost
  port: 6379

postgres:
  host: localhost
  port: 5432
  database: mediaflow

minio:
  endpoint: localhost:9000
  input_bucket: mediaflow-input
  processing_bucket: mediaflow-processing
  output_bucket: mediaflow-output
  clips_bucket: mediaflow-clips
```

Env vars override config at runtime: `PIPELINE_MAX_RETRIES`, `PIPELINE_JOB_TIMEOUT_SEC`,
`PIPELINE_MAX_CONCURRENT_JOBS`, `DAGSERVICE_URL`, `MINIO_ENDPOINT`, etc.

**Worker resource limits** (prevent CPU exhaustion on the host):

| Env var | Default | Effect |
|---|---|---|
| `WORKER_CPU_THREADS` | `2` | FFmpeg `-threads` + Demucs/PyTorch `OMP_NUM_THREADS` |
| `WORKER_NICE` | `10` | OS scheduling priority (0 = normal, 19 = lowest) |

```bash
# Example: allow more CPU, higher priority
WORKER_CPU_THREADS=4 WORKER_NICE=5 bash scripts/ctl.sh start worker
```

---

## How to Run (Development)

```bash
# 1. Copy and edit config
cp config.yaml.example config.yaml

# 2. Start everything (Docker + Whisper + Watcher + Worker)
make start
# or: bash scripts/ctl.sh start all

# 3. Frontend dev server
cd frontend && npm run dev
# Web: http://localhost:3000   API: http://localhost:8080
```

### Service Control

`Makefile` is the primary interface; `scripts/ctl.sh` is the implementation.

```bash
make status                    # show all service status + health
make start                     # start all (Docker + Whisper + Watcher + Worker)
make stop                      # stop all
make restart                   # stop + start all
make restart-worker            # restart just the worker (e.g. after code change)
make logs-worker               # tail worker log
make logs-api                  # tail API (Docker) log
make rebuild                   # rebuild Docker images + restart everything
make start-diarize             # optional: speaker diarization :9003
```

Full service list for `start/stop/restart/logs`:
`all` · `docker` · `whisper` · `watcher` · `worker` · `diarize` · `api` · `web` · `redis`

```bash
# Direct ctl.sh (equivalent)
bash scripts/ctl.sh restart worker
bash scripts/ctl.sh logs    worker
bash scripts/ctl.sh rebuild api
```

### Smoke Test

```bash
bash tests/run-pipeline-test.sh
```

Expected: 5 passed, 0 failed (SRT, summary, JSON, archive, content check).

### Python Environment

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

### Useful Commands

```bash
# Local re-run from a specific stage (dev only, uses local workspace)
source venv/bin/activate
python -m pipeline.rerun --stem lesson01 --from-stage transcribe

# API health
curl http://localhost:8080/health
curl http://localhost:8080/jobs | python3 -m json.tool

# Watch Redis MQ
redis-cli XREAD COUNT 10 STREAMS mediaflow:jobs 0
```

---

## Coding Conventions

- **No comments** unless the WHY is non-obvious. Never narrate what the code does.
- **Blocking functions** (FFmpeg, httpx, ollama) belong in `pipeline/` and must run in the worker's thread pool — never in async context.
- **Async functions** belong in `api/`. Use `asyncpg` for DB access.
- **DAG orchestration** lives in `api/services/dag.py`. Both HTTP callbacks and retry logic call functions there — do not duplicate.
- **Project Service** (`api/services/project.py`) owns job creation + FR6 check. Always goes through `on_upload_trigger()`.
- **No Redis on the API side except** `api/main.py` (connection setup) and `api/services/dag.py` (`xadd`).
- **`config.yaml` is gitignored**. `config.yaml.example` is the committed template.
- **`models/` is gitignored**. Run `scripts/download-models.sh` after cloning.
- Do not push to remote unless explicitly asked.

---

## Version Control

Trunk-based development on `main`. See [`docs/git-workflow.md`](docs/git-workflow.md) for branching rules, commit format, and tagging procedure. See [`docs/releases.md`](docs/releases.md) for the current unreleased changelog and release history.

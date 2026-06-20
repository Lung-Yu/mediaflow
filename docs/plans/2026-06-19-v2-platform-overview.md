# V2 Platform Reimplementation — Master Overview

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement each sub-plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transition mediaflow from monolithic host-native pipeline to microservices architecture — PostgreSQL, MinIO (4 buckets), Project Service (intake + FR6), DAG-Service (orchestration + retry), Progress Worker (stage execution), and a provider pattern that lets you swap model backends via config without touching pipeline code.

**Architecture:** Each sub-plan produces independently testable, deployable software. Sub-plans A and B are independent foundations that can execute in parallel. C–G layer on top with clear dependency gates.

**Tech Stack:** FastAPI, asyncpg (PostgreSQL), boto3 (MinIO/S3), Redis Streams (MQ), pytest-asyncio, Docker Compose

## Global Constraints

- Python ≥ 3.11
- All async DB calls use `asyncpg` pool — no aiosqlite in new code
- All file I/O between services goes through MinIO — never direct filesystem between containers
- Provider field in stage config selects backend; adding a new provider = new class, no branch in pipeline code
- PostgreSQL DSN: `postgresql://mediaflow:changeme@postgres:5432/mediaflow` (Docker) / `localhost:5432` (host)
- MinIO internal endpoint: `minio:9000`; public endpoint: `localhost:9000` (or `MINIO_PUBLIC_ENDPOINT`)
- MQ key: `mediaflow:jobs` (new stream, separate from legacy `mediaflow:events`)
- All tests mock at the module boundary (`api.db`, `api.minio_client`) — no real I/O in unit tests
- Commit after every task; branch `feat/v2-platform`
- No force-push to main; merge only when user confirms

---

## Sub-plan Dependency Graph

```
A (PostgreSQL)  ──────────────────────────────────────────────┐
B (Provider Pattern) ─────────────────────────────────────┐   │
                                                           ↓   ↓
                                              C (DAG-Service)
                                                    │
                                                    ↓
                                         D (Progress Worker)  ← B
                                                    │
                          E (Project Service + FR6) ┤
                                                    │
                                    F (FR4 + Clip)  ┤
                                                    │
                                    G (Web Updates) ┘
```

**Execute in order:**
1. **A** and **B** in parallel (no dependencies)
2. **C** (depends on A)
3. **D** (depends on A, B, C)
4. **E** (depends on A, C)
5. **F** (depends on A, C, D)
6. **G** (depends on E — needs new API routes)

---

## Sub-plans Index

| File | Subsystem | Blocks | Deliverable |
|------|-----------|--------|-------------|
| [v2-A-postgres.md](2026-06-19-v2-A-postgres.md) | PostgreSQL + dag_flows | C, D, E, F | asyncpg pool, jobs/dag_flows/events tables, seed data |
| [v2-B-provider-pattern.md](2026-06-19-v2-B-provider-pattern.md) | Provider Pattern | D | `pipeline/providers/` — whisper/llm/diarize backends |
| [v2-C-dag-service.md](2026-06-19-v2-C-dag-service.md) | DAG-Service | D, E | Orchestration endpoints, retry logic, new MQ schema |
| [v2-D-progress-worker.md](2026-06-19-v2-D-progress-worker.md) | Progress Worker | F | MQ consumer, MinIO I/O, sequential stage runner |
| [v2-E-project-service.md](2026-06-19-v2-E-project-service.md) | Project Service + FR6 | F, G | POST /jobs, FR6 check, MinIO processing/ staging |
| [v2-F-features.md](2026-06-19-v2-F-features.md) | FR4 + Segment Clip | G | Correction endpoints, on-demand clip API |
| [v2-G-web.md](2026-06-19-v2-G-web.md) | Web Updates | — | Dashboard + SRT viewer with audio playback |

---

## Global File Structure Map

```
# New files
api/db/
  __init__.py          — re-export public query functions
  queries.py           — asyncpg implementations (replaces api/db.py)
  migrations/
    001_init.sql       — jobs, dag_flows, events tables
    002_seed_flows.sql — course-v1, meeting-v1, general-v1

api/services/
  __init__.py
  project.py           — job intake, FR6 check, MinIO verify, DAG trigger
  dag.py               — dag_flows lookup, MQ enqueue, retry logic

api/routes/
  jobs.py              — POST /jobs, GET /jobs/{id}, GET /jobs
  correction.py        — PATCH /jobs/{id}/correction, POST .../finalize
  clip.py              — GET /jobs/{id}/segment/{index}/audio

pipeline/providers/
  __init__.py          — factory: get_whisper_provider(), get_llm_provider(), get_diarize_provider()
  base.py              — WhisperProvider, LLMProvider, DiarizeProvider ABCs
  whisper.py           — MlxWhisperProvider, FasterWhisperProvider, OpenAIWhisperProvider
  llm.py               — OllamaProvider, OpenAILLMProvider
  diarize.py           — SpeechbrainProvider, PyannoteProvider

pipeline/worker.py     — Progress Worker: MQ consumer + sequential stage runner + MinIO I/O

# Modified files
docker-compose.yml     — add postgres service; add processing/clips bucket init
requirements.txt       — add asyncpg; keep aiosqlite until Sub-plan A completes
config.yaml.example    — add postgres section; update stage config with provider fields
api/main.py            — init asyncpg pool in lifespan; remove sqlite init
api/minio_client.py    — add processing_bucket, clips_bucket; add clip(), copy_input_to_processing()
pipeline/stages.py     — accept provider instances instead of calling services directly
pipeline/runner.py     — accept stage_plan list from MQ message; remove _DEFAULT_STAGES

# Deleted after migration complete
api/db.py              — replaced by api/db/queries.py
api/mq/queue_consumer.py — replaced by pipeline/worker.py
```

---

## MinIO Bucket Layout (final state)

| Bucket env var | Default name | TTL | Contents |
|---|---|---|---|
| `MINIO_INPUT_BUCKET` | `mediaflow-input` | 1 h | Raw uploads from browser/watcher |
| `MINIO_PROCESSING_BUCKET` | `mediaflow-processing` | 7 d | Staged input + intermediates (_segments.json, _clean.wav) |
| `MINIO_OUTPUT_BUCKET` | `mediaflow-output` | forever | SRT, _summary.md/json, _chapters.json, _clean.wav |
| `MINIO_CLIPS_BUCKET` | `mediaflow-clips` | 10 min | On-demand segment clips |

---

## PostgreSQL Schema Summary

```sql
-- jobs
id TEXT PK, filename TEXT, submitted_by TEXT DEFAULT 'anonymous',
dag_flow_id TEXT REFERENCES dag_flows(id),
status TEXT CHECK(status IN ('submitted','queued','processing','completed','failed')),
current_stage TEXT, submitted_at REAL, started_at REAL, completed_at REAL,
retry_count INT DEFAULT 0, error_msg TEXT,
output_srt_path TEXT, corrected_srt_path TEXT,
verification_status TEXT DEFAULT 'unverified'
  CHECK(verification_status IN ('unverified','in_progress','verified')),
verified_at REAL, verified_by TEXT,
minio_input_key TEXT, minio_processing_key TEXT

-- dag_flows
id TEXT PK, stage_plan JSONB NOT NULL, is_default BOOL DEFAULT false,
deprecated BOOL DEFAULT false, created_at REAL

-- events
id SERIAL PK, job_id TEXT REFERENCES jobs(id) NOT NULL,
stage TEXT NOT NULL, status TEXT CHECK(status IN ('started','success','failed')),
retry_attempt INT DEFAULT 0, error_msg TEXT, payload TEXT, ts REAL NOT NULL
```

---

## MQ Message Schema (new stream: `mediaflow:jobs`)

```python
# DAG-Service → Progress Worker (via Redis XADD)
{
    "job_id":             "abc123",
    "processing_path":    "processing/abc123/lesson01.wav",
    "stage_plan":         '[{"stage":"preprocess","config":{"provider":"ffmpeg"}}, ...]',
    "retry_attempt":      "0",          # Redis values are strings
    "resume_from_stage":  "preprocess"
}

# Progress Worker → DAG-Service (via HTTP POST)
POST /internal/stage-callback
{
    "job_id":         "abc123",
    "stage":          "transcribe",
    "status":         "success",        # or "failed"
    "retry_attempt":  0,
    "error_msg":      null,
    "output_keys":    ["output/abc123/lesson01.srt"]
}
```

---

## Provider Config Schema (stage_plan[].config)

```python
# In dag_flows.stage_plan JSONB:
{"stage": "transcribe", "config": {
    "provider": "mlx-whisper",  # | "faster-whisper" | "openai"
    "language": "zh",
    "model":    "medium"
}}
{"stage": "summarize", "config": {
    "provider":        "ollama",  # | "openai"
    "model":           "qwen2.5:7b",
    "prompt_key":      "summarize",
    "recording_type":  "course"
}}
{"stage": "diarize", "config": {
    "provider":       "speechbrain",  # | "pyannote"
    "num_speakers":   null,
    "speaker_format": "【{label}】"
}}
```

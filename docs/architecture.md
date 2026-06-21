# MediaFlow — Architecture Reference

> Copied from `docs/architecture.excalidraw` for cross-reference during implementation review.

---

## System Architecture

```
User
 ├── Folder Watcher          submitted_by: 'anonymous', pipeline_config: system defaults
 │    └── uploads to MinIO input/ first → same flow as front-end upload
 └── front-end website
      └── presigned URL upload to MinIO input/
              ↓
      Both paths → Project Service
                        ↓
                   DAG-Service (pipeline)
                        ↓
                   MQ (redis stream)
                        ↓
                   Progress Worker ──── 1. download file from processing/
                        │               2. upload result files to output/
                        └── update-status → DAG-Service (HTTP callback)

FileStorage (MinIO) ← shared by all services
RDB (PostgreSQL)    ← Project Service + DAG-Service reads/writes
```

> FR6 security check applies to both paths (Folder Watcher and front-end upload).

---

## MinIO Buckets

Lifecycle managed by object storage TTL policies:

| Bucket | TTL | Contents |
|--------|-----|----------|
| `input/` | 1h | Raw upload; Project Service copies to `processing/` then TTL cleans up |
| `processing/` | 7d | Staged input + intermediates: `segments.json`, verification outputs, `clean.wav` |
| `output/` | forever | `{job}.srt`, `_summary.md/json`, `_corrected.srt`, `clean.wav` |
| `clips/` | 1d object lifecycle | On-demand segment audio clips; presigned URLs expire 1h |

---

## Requirements

### Functional Requirements

**FR1 — File Ingestion**
User can submit audio/video files via:
- Local folder (`workspace/1_input/`)
- Web browser upload (MinIO presigned multipart) ⚠️ not yet built

**FR2 — Audio Analysis Pipeline**
System processes uploaded files through a configurable pipeline:
- Transcription → time-stamped transcript (`.srt`) — always on
- Speaker diarization → speaker labels per segment — toggleable per job
- Summarization → overview, key moments, topic segments — always on

**FR3 — Output Access & Export**
- Web interface: view transcript, summary, speaker labels
- Export: download SRT, summary (MD / JSON), or bundled ZIP
- Push notification: out of scope

**FR4 — Transcript Correction** ⚠️ not yet built
- User can play back individual audio segments from the web interface
- User can correct transcript text inline while listening
- Corrections are saved and reflected in the exported SRT

**FR5 — Reliability & Audit** ⚠️ not yet built
- System auto-retries failed jobs up to N times (default 3, configurable)
- Retry history and error logs are visible and queryable in the web UI

**FR6 — Security Validation Step** ⚠️ not yet built
- Dedicated check before triggering pipeline (toggleable)
- Phase 1: filename anomaly + file size out-of-range detection
- Reject and notify Project Service if check fails
- Future: content scanning, malware check (pluggable)

---

### Non-Functional Requirements

**NFR1 — GPU-First**
Use GPU when available; gracefully fall back to CPU.
> Exception: Diarize currently forced CPU (speechbrain MPS upstream bug)

**NFR2 — Always-On Access**
Processed results queryable at any time; full-text search across all files.

**NFR3 — No SPOF**
Each service component must tolerate individual failure without total system loss.
> Current risk: Redis single instance, PostgreSQL single node — needs mitigation design

**NFR4 — Scale-Out Ready**
API/Web layer designed for horizontal scaling.
Current throughput bounded by single Mac mini; max concurrent pipeline runs configurable.

**NFR5 — Future Multi-User Extensible**
Architecture must not preclude adding auth / user management later.
Not implemented now; single-user tool in current phase.

**NFR6 — Configurability**
Per-job feature toggles (diarization, output format, sensitivity).
Global defaults in `config.yaml`; retry count configurable.

---

## Design

### Project Service

**On upload trigger:**
1. Verify file exists in MinIO `input/` (guard: upload may succeed but notify fail → let TTL clean up)
2. FR6 security check: filename anomaly + size validation
3. Copy `input/{job}` → `processing/{job}` ← 7d TTL; eliminates retry race
4. If ok → create job in RDB → trigger DAG-Service
5. If fail → reject, clean up `processing/`, do not trigger pipeline

**Correction write path (FR4):**
- `PATCH /jobs/{id}/correction` received
  - If `unverified` → auto-set `verification_status = in_progress`
  - Rebuild corrected SRT from segments array
  - Write MinIO `output/{job}_corrected.srt`
  - Update `jobs.corrected_srt_path` in RDB

### MQ Message Schema

```json
{
  "job_id": "...",
  "processing_path": "processing/{job}",
  "stage_plan": ["preprocess", "transcribe", "..."],
  "retry_attempt": 0,
  "resume_from_stage": "preprocess"
}
```

---

## API Design

```
POST   /jobs
       body: { file_key, dag_flow?: "course-v1" }  ← omit = default

GET    /jobs/{id}
       returns: status, stages, events (audit)

PATCH  /jobs/{id}/correction
       body: { segments: [{index, text}] }  ← full replace

POST   /jobs/{id}/correction/finalize
       → verification_status: verified, verified_at: now

GET    /jobs/{id}/events
       → [{stage, status, retry_attempt, ts, error_msg}]

GET    /jobs/{id}/export?format=srt&version=corrected|original

GET    /jobs/{id}/segment/{index}/audio
       → presigned URL of clipped clean.wav
       on-demand clip flow:
         1. check clips/{job_id}/{index}.wav in MinIO (10min TTL)
         2. cache hit  → return presigned URL immediately
         3. cache miss → ffmpeg clip output/{job}_clean.wav
                         by segment timestamps from _segments.json
                       → upload to clips/{job_id}/{index}.wav
                       → return presigned URL
```

---

## Database Schema

> PostgreSQL — Phase 1: single entity; split later

### jobs

```
id                  TEXT    PK
filename            TEXT    NOT NULL
submitted_by        TEXT    DEFAULT 'anonymous'
dag_flow_id         TEXT    FK → dag_flows.id
status              TEXT    NOT NULL
                    submitted | queued | processing | completed | failed
current_stage       TEXT
submitted_at        REAL
started_at          REAL
completed_at        REAL
retry_count         INT     DEFAULT 0
error_msg           TEXT
output_srt_path     TEXT
corrected_srt_path  TEXT
verification_status TEXT    DEFAULT 'unverified'
                    unverified | in_progress | verified
verified_at          REAL
verified_by          TEXT    ← reserved for multi-user
minio_input_key      TEXT    ← original upload key in input/ bucket
minio_processing_key TEXT    ← copied key in processing/ bucket (retry source)
initial_prompt       TEXT    DEFAULT ''  ← per-request Whisper warm-up vocab
```

### dag_flows

```
id          TEXT   PK   e.g. "course-v1"
stage_plan  JSONB  NOT NULL  [{stage, config}]
is_default  BOOL   DEFAULT false
deprecated  BOOL   DEFAULT false
created_at  REAL

▎ Phase 1: seed data only
▎ Phase 2: API-manageable
```

### events (stage-level audit)

```
id             SERIAL  PK
job_id         TEXT    FK → jobs.id  NOT NULL
stage          TEXT    NOT NULL
status         TEXT    started | success | failed
retry_attempt  INT     DEFAULT 0
error_msg      TEXT
payload        TEXT    — full event JSON
ts             REAL    NOT NULL  (Unix epoch seconds)
```

### verification_status flow

```
unverified  → (job created)
in_progress → (first PATCH /correction, auto-set)
verified    → POST .../finalize
▎ finalize also valid from unverified (no edits needed)
```

---

## DAG-Service Internals

### 1. On trigger (from Project Service)

- `jobs`: status=queued, retry_count=0, dag_flow_id
- Query `dag_flows` by `dag_flow_id` (if omitted → `WHERE is_default=true`)
- `stage_plan = dag_flows.stage_plan`
- Enqueue MQ: `{ job_id, stage_plan, retry_attempt: 0, resume_from_stage: "preprocess" }`

### 2. On stage callback (HTTP from Progress Worker)

- Append `events`: `{ stage, status, retry_attempt, ts }`
- All stages success? → `jobs.status = completed`
- Stage failed? → retry decision

### 3. Retry decision

```
retry_count < max  → retry_count++
                     re-enqueue resume_from_stage: failed_stage
                     ▎ Worker overwrites existing stage outputs (idempotent)
retry_count >= max → jobs.status = failed
                     webhook notify → Project Service
```

---

## Stage Plan

Built from `pipeline_config`:

```
Always:   preprocess → transcribe → summarize → [detect_chapters]

Optional (injected sequentially, in this order):
  [verify_segments]  — after transcribe
  [diarize]          — after verify_segments (or transcribe)
                       ▎ speaker labels inserted before correct_srt;
                         helps LLM resolve speaker-specific homophones
  [correct_srt]      — after diarize (or verify_segments / transcribe)
```

All stages run sequentially (no fork/join in Phase 1).

`resume_from_stage` skips all stages before the failed node, then re-runs failed stage + all downstream.
- Stage outputs in MinIO `processing/` are overwritten on retry (idempotent)
- Upstream outputs are preserved — no re-run of already-passed stages

---

## Preset DAG Flows

Seed data for `dag_flows` table (Phase 1):

| Flow | Stage sequence |
|------|----------------|
| `course-v1` | preprocess → transcribe → verify_segments → correct_srt → summarize → detect_chapters |
| `meeting-v1` | preprocess → transcribe → diarize → summarize |
| `general-v1` *(default)* | preprocess → transcribe → summarize |

**Selection logic** (Project Service, at job creation):
- `POST /jobs { dag_flow: "course-v1" }` → use specified flow
- `POST /jobs { }` → use `dag_flows.is_default` row
- No filename pattern matching — caller decides or falls back to default

---

## Stage Plan Config Schema

> `provider` field lets you swap model/service per stage without touching pipeline code — hardware-driven choice.

**preprocess**
```yaml
provider: "ffmpeg"   # hardcoded chain; FFmpeg params TBD
```

**transcribe / verify_segments**
```yaml
provider: "mlx-whisper"     # Apple Silicon GPU (default)
        | "faster-whisper"  # CPU / CUDA
        | "openai"          # remote API fallback
language: "zh"
model: "medium" | "large-v3"
```

**correct_srt / summarize / detect_chapters**
```yaml
provider: "ollama"    # local LLM (default)
        | "openai"    # remote API fallback
model: "qwen2.5:7b"  # ollama tag or openai model id
prompt_key: "correct_srt" | "summarize" | "detect_chapters"
recording_type: "course"   # summarize only
min_gap_sec: 30            # detect_chapters only
```

**diarize**
```yaml
provider: "speechbrain"  # Apache 2.0, no token (default)
        | "pyannote"     # HuggingFace token required
num_speakers: null        # null = auto-detect
speaker_format: "《{label}》"
```

---

## Concurrency Limits

`config.yaml` (all required):

```yaml
pipeline:
  max_concurrent_jobs: 2   # Progress Worker thread pool size
                           # scale up when hardware has more GPU/CPU/mem
  max_queue_depth:    20   # MQ pending cap; reject POST /jobs if exceeded
  max_retries:         3   # per job, before final failure
  retry_backoff_sec:  30   # wait before re-enqueue
```

- `in-flight` = jobs WHERE status IN (`queued`, `processing`)
- DAG-Service rejects `POST /jobs` if `in-flight >= max_concurrent_jobs`
- `queued` jobs count against limit (resources reserved at enqueue time)
- `max_concurrent_jobs` is the primary resource cap; `max_queue_depth` is the MQ backpressure cap (separate concern)

---

## Progress Worker

1. Ack MQ immediately on receive (retry owned by DAG-Service, not MQ redelivery)
2. Download from `processing/{job}` ← staged by Project Service at job creation
3. Run pipeline stages sequentially from `resume_from_stage` (no fork/join, Phase 1)
4. On each stage: `update-status(stage, result)` → DAG-Service via HTTP
5. On complete: upload (overwrite) → `output/`
   - `{job}.srt`, `_summary.md/json`, `_segments.json`
   - `clean.wav` ← kept forever for segment playback (on-demand clip source)
   - Overwrite is safe: outputs are idempotent across retries
6. On stage failure: `update-status(failed)` → DAG-Service; DAG-Service decides: retry (re-enqueue) or final failure

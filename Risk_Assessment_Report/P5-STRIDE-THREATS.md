# P5 — STRIDE Threat Analysis

---

## Spoofing

| ID | Threat | Target | Attack Path | Likelihood | Impact |
|---|---|---|---|---|---|
| S1 | Worker identity spoofing | `/internal/stage-callback` | Any process on the network POSTs to `http://host:8080/internal/stage-callback` with an arbitrary `job_id` and `status: "success"` — no shared secret required | High | High |
| S2 | User identity spoofing | All job operations | `submitted_by` is a free-form string supplied by the caller; any caller can claim any identity | High | Low (field unused for authz) |
| S3 | Job ID guessing | `GET/DELETE /jobs/{id}` | Job IDs are `{8_hex}_{stem}` — 8 hex chars = 4 billion combinations, but stems leak in filenames, reducing search space | Medium | Medium |

---

## Tampering

| ID | Threat | Target | Attack Path | Likelihood | Impact |
|---|---|---|---|---|---|
| T1 | Redis stream injection | Pipeline job queue | Connect to Redis port 6379 (no auth) and `XADD mediaflow:jobs` with crafted `processing_path` pointing to attacker-controlled MinIO key | Medium | **Critical** |
| T2 | Fake stage callback | Job state machine | POST `/internal/stage-callback` with `job_id=X, stage=summarize, status=success` to short-circuit the pipeline — job marked complete without processing | High | High |
| T3 | Transcript tampering | Correction endpoint | `PATCH /jobs/{any_id}/correction` with arbitrary segment data — no auth, no ownership check | High | High |
| T4 | Segments.json poisoning | Clip generation | If attacker can write to MinIO output/, they can inject malicious `start`/`end` values into `_segments.json`; these feed into FFmpeg CLI args (though float-cast provides some protection) | Low | Medium |
| T5 | DAG flow hijack | Job pipeline config | `POST /jobs { dag_flow: "meeting-v1" }` — caller controls which stage sequence runs on any uploaded file | High | Low (limited to existing flows) |

---

## Repudiation

| ID | Threat | Target | Attack Path | Likelihood | Impact |
|---|---|---|---|---|---|
| R1 | Deniable uploads | Audit log | All uploads are `submitted_by: anonymous` — no way to attribute who submitted a job | High | Medium |
| R2 | Untracked corrections | Transcript integrity | `verified_by` column is always empty; no record of who finalized a correction | High | Low (single user now) |
| R3 | Job deletion removes audit trail | Events table | `DELETE /jobs/{id}` also deletes all stage events (`events` table cascade) — no audit log preserved | High | Medium |

---

## Information Disclosure

| ID | Threat | Target | Attack Path | Likelihood | Impact |
|---|---|---|---|---|---|
| I1 | Full job list exposure | `GET /jobs` | Unauthenticated — returns all jobs, filenames, processing keys, and error messages to any caller | High | High |
| I2 | Audio content exposure via MinIO paths | MinIO buckets | `minio_processing_key` and `minio_input_key` stored in DB and returned in job records — leak internal bucket paths | High | Medium |
| I3 | Presigned URL abuse | MinIO clips/ | 1-hour presigned GET URLs for segment audio can be shared/bookmarked; CORS `*` allows cross-origin embedding | Medium | Medium |
| I4 | Error message leakage | API 400/500 responses | `HTTPException(400, f"File not found in storage: {exc}")` — exception strings may expose internal paths or stack info | Medium | Low |
| I5 | Grafana dashboard exposure | Monitoring | Port 3001 with default `admin` password exposes all Prometheus metrics, job counts, latency data | High | Medium |
| I6 | MinIO console exposure | Storage admin | Port 9002 with default credentials gives full bucket browser access including all audio files | High | **Critical** |
| I7 | `initial_prompt` exfiltration | Pipeline telemetry | `initial_prompt` is stored in DB and returned in job records — if it contains sensitive vocab hints, it's now in the job response | Medium | Low |

---

## Denial of Service

| ID | Threat | Target | Attack Path | Likelihood | Impact |
|---|---|---|---|---|---|
| D1 | Job queue flooding | Pipeline capacity | POST /jobs with 20 messages (max_queue_depth) — fills queue, blocks all legitimate processing | High | High |
| D2 | Bulk job deletion | All jobs | Loop `DELETE /jobs/{id}` with enumerated IDs — destroys all job records and audit history | Medium | High |
| D3 | Storage exhaustion | MinIO | Repeatedly call POST /upload/init + upload 5GB files to fill disk | Medium | High |
| D4 | Crafted media bombs | Worker / FFmpeg | Upload a decompression-bomb audio file that expands to terabytes during FFmpeg preprocessing | Low | High |
| D5 | Whisper resource exhaustion | Whisper service | Submit many long audio files simultaneously (queue up to `max_queue_depth=20`) — Whisper runs on GPU, starving other jobs | Medium | Medium |
| D6 | Watchdog amplification | Retry loops | Set `started_at` to null via Redis injection → watchdog re-enqueues job every hour indefinitely | Low | Medium |

---

## Elevation of Privilege

| ID | Threat | Target | Attack Path | Likelihood | Impact |
|---|---|---|---|---|---|
| E1 | Pipeline stage injection via Redis | Worker execution | Inject XADD with `processing_path: processing/../../etc/passwd` or a path pointing to attacker-controlled content — Worker downloads and passes to FFmpeg/Whisper | Medium | High |
| E2 | SSRF via webhook | API container | If `WEBHOOK_URL` can be influenced (e.g., DB compromise sets job metadata that feeds into webhook), force API container to POST to internal services | Low | High |
| E3 | Ollama prompt injection | LLM output | Craft audio whose transcript contains adversarial instructions that override the system prompt in `correct_srt` or `summarize` stages — may cause LLM to output code or exfiltrate context | Medium | Medium |
| E4 | Rerun any job | Pipeline | `POST /jobs/{id}/rerun` is unauthenticated — any caller can re-trigger pipeline on any completed job, consuming resources and overwriting output | High | Medium |
| E5 | Correction finalization bypass | Verification status | `POST /jobs/{id}/correction/finalize` is unauthenticated — anyone can mark any job as `verified` | High | Low (single user now) |
| E6 | MinIO admin via default creds | All buckets | Default MinIO credentials allow full S3 API access including deleting buckets, reading all audio | High | **Critical** |

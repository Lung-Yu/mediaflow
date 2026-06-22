# P1 — Project Understanding

**Project**: MediaFlow  
**Date**: 2026-06-23  
**Methodology**: STRIDE Threat Modeling (fr33d3m0n/threat-modeling v3.2.0)

---

## System Profile

| Attribute | Value |
|---|---|
| Type | Audio recording pipeline — transcription + summarization |
| Deployment | Single Mac mini (Apple Silicon); Docker Compose for infra services |
| Auth model | **None** — single-user, unauthenticated |
| Users | Local operator (single user, assumed trusted) + web browser upload |
| Data sensitivity | Audio recordings, transcripts, summaries; may contain PII |
| Internet exposure | API port 8080 exposed; MinIO presigned URLs exposed to browser |

---

## Components Inventory

| Component | Runtime | Port | Auth |
|---|---|---|---|
| FastAPI (api) | Docker | 8080 | None |
| PostgreSQL | Docker | 5432 | Password (`changeme` default) |
| Redis | Docker | 6379 | None |
| MinIO | Docker | 9000, 9002 | Key/Secret (`changeme` default) |
| Whisper HTTP | Host (mlx) | 9001 | None |
| Ollama | Host | 11434 | None |
| Grafana | Docker | 3001 | Password (`admin` default) |
| React frontend | Docker/Vite | 3000 | None |
| Worker | Host process | — | None |
| Watcher | Host process | — | None |
| Diarize (optional) | Host | 9003 | None |

---

## Entry Points

| ID | Entry Point | Location | Input Type |
|---|---|---|---|
| EP1 | `POST /upload/init` | `api/routes/upload.py:37` | filename, size_bytes, content_type |
| EP2 | `POST /upload/complete` | `api/routes/upload.py:50` | upload_id, minio_key, parts, initial_prompt |
| EP3 | `POST /jobs` | `api/routes/jobs.py:26` | file_key, filename, dag_flow, submitted_by |
| EP4 | `DELETE /jobs/{id}` | `api/routes/jobs.py:44` | job_id path param |
| EP5 | `POST /jobs/{id}/rerun` | `api/routes/jobs.py:53` | job_id path param |
| EP6 | `PATCH /jobs/{id}/correction` | `api/routes/correction.py:28` | job_id, segments array |
| EP7 | `POST /jobs/{id}/correction/finalize` | `api/routes/correction.py:33` | job_id |
| EP8 | `GET /jobs/{id}/segment/{index}/audio` | `api/routes/clip.py:60` | job_id, segment index |
| EP9 | `POST /internal/stage-callback` | `api/routes/dag_callback.py:27` | job_id, stage, status, error_msg |
| EP10 | MinIO presigned PUT URLs | generated at EP1 | raw file bytes from browser |
| EP11 | MinIO presigned GET URLs | generated at EP8 | served to browser |
| EP12 | Folder watcher (`workspace/1_input/`) | `pipeline/watcher.py` | file drop |
| EP13 | Redis stream `mediaflow:jobs` | `api/services/dag.py` | XADD message |

---

## Data Assets

| Asset | Location | Sensitivity |
|---|---|---|
| Audio files | MinIO `input/`, `processing/`, `output/` | High — may contain private conversations |
| Transcripts (SRT) | MinIO `output/` | High |
| Summaries (MD/JSON) | MinIO `output/` | Medium |
| Segment audio clips | MinIO `clips/` | High |
| Job metadata | PostgreSQL `jobs` table | Medium |
| Stage events / audit log | PostgreSQL `events` table | Low |
| Pipeline config | `config.yaml` on host | Medium (contains service credentials) |
| MinIO credentials | env vars / docker-compose | Critical |
| DB password | env var / docker-compose | Critical |

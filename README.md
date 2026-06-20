# mediaflow

Audio recording pipeline → transcript → structured knowledge base.

Watches a folder for audio/video files, runs them through speech enhancement → Whisper transcription → LLM summarization, and serves results through a live web dashboard with full-text search and click-to-play audio.

**Features**
- Drop files into `workspace/1_input/` — pipeline runs automatically
- FFmpeg speech-enhancement preprocess (9-stage filter chain)
- Whisper transcription → optional LLM correction → speaker diarization → summarization → chapter detection
- SRT transcript viewer with **per-segment audio playback** via presigned MinIO URLs
- **Analytics panel**: aggregate counts, per-speaker time breakdown, keyword trends
- REST API for automation and AI agent integration
- Provider abstraction — swap Whisper model or LLM without touching pipeline code

## Architecture

```
[Host — native, Apple Silicon GPU-bound]
  pipeline/watcher.py        — watchdog + ThreadPoolExecutor (1 job at a time)
  pipeline/runner.py         — stage DAG executor
  pipeline/stages.py         — preprocess / transcribe / summarize / diarize / …
  pipeline/providers/        — WhisperProvider / LLMProvider / DiarizeProvider

  External services (host-native, GPU access):
    whisper/service.py   :9001   — mlx-whisper HTTP API
    ollama               :11434  — qwen2.5:7b (or any pulled model)

                ↕ Redis Streams (mediaflow:events)

[Docker Compose]
  postgres  :5432   — job state (asyncpg)
  redis     :6379   — event MQ (appendfsync always)
  minio     :9000   — object store: input / processing / output / clips
  api       :8080   — FastAPI: job intake, DAG service, REST endpoints
  web       :3000   — Jinja2 + HTMX dashboard + SRT viewer
```

**Why this split**: mlx-whisper and Ollama need Apple Silicon GPU and cannot run in Docker.  
API + Web are fully containerized and can be deployed to a remote server.

## Quick Start

```bash
# 1. Copy config
cp config.yaml.example config.yaml   # edit workspace_dir, model names, etc.

# 2. Start everything
make start          # or: bash scripts/ctl.sh start all

# 3. Drop a file
cp recording.m4a workspace/1_input/
```

Web UI: http://localhost:3000  
API:    http://localhost:8080  
MinIO:  http://localhost:9002  (console)

### Service Control

```bash
make status              # show all service health
make start / stop        # all services
make restart-whisper     # restart Whisper after model change
make logs-watcher        # tail pipeline log
make rebuild-api         # rebuild + restart API container

# Or use ctl.sh directly:
bash scripts/ctl.sh start whisper
bash scripts/ctl.sh logs api
```

### Re-run a Stage

```bash
# Re-run from transcription (skips FFmpeg/Demucs — WAV already exists)
source venv/bin/activate
python -m pipeline.rerun --stem lesson01 --from-stage transcribe

# Re-run summary only (tune prompts without re-transcribing)
python -m pipeline.rerun --stem lesson01 --from-stage summarize
```

## Requirements

| Component | Version |
|-----------|---------|
| Python | 3.11+ |
| Docker or Podman | any recent |
| Ollama | running on :11434; `ollama pull qwen2.5:7b` |
| Apple Silicon | required for mlx-whisper |

## Project Structure

```
pipeline/
  watcher.py          — watchdog loop + ThreadPoolExecutor (max_workers=1)
  runner.py           — stage DAG executor
  stages.py           — all pipeline stages
  providers/          — WhisperProvider, LLMProvider, DiarizeProvider ABCs + impls
  prompts.yaml        — all Ollama prompt templates (edit to tune, no code change)
  rerun.py            — CLI: re-run from any stage

whisper/
  service.py          — FastAPI Whisper HTTP wrapper (mlx-whisper, Apple Silicon)
  requirements.txt    — venv-whisper deps

api/
  main.py             — FastAPI lifespan: asyncpg pool + Redis consumer
  db/                 — asyncpg queries + migrations (PostgreSQL)
  services/           — DAG service, correction service
  routes/             — /jobs, /files, /status, /clips

web/
  main.py             — FastAPI + Jinja2 + httpx proxy to API
  templates/          — dashboard, SRT viewer, upload

diarize/              — optional speaker diarization service :9003 (speechbrain)
scripts/
  ctl.sh              — service control (start/stop/restart/rebuild/logs/status)
config.yaml           — gitignored; copy from config.yaml.example
docker-compose.yml    — postgres + redis + minio + api + web
Makefile              — convenience wrapper for ctl.sh
```

## Configuration

Key settings in `config.yaml`:

```yaml
pipeline:
  workspace_dir: ./workspace        # where files are dropped + outputs written

whisper:
  service_url: http://localhost:9001
  language: zh                      # transcription language

ollama:
  service_url: http://localhost:11434
  model: qwen2.5:7b

# Optional stages (disabled by default):
#   pipeline.stages[correct_srt].enabled: true
#   pipeline.stages[diarize].enabled: true
#   pipeline.stages[verify_segments].enabled: true
```

Whisper model is set via `WHISPER_MODEL` env var in `ctl.sh` (default: `mlx-community/whisper-medium-mlx`).

## Smoke Test

```bash
# Prerequisites: make start must be running
bash tests/run-pipeline-test.sh
```

Expected: `=== Result: 6 passed, 0 failed ===`

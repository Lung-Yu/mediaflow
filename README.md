# mediaflow

Audio recording pipeline → transcript → knowledge base.

Processes audio/video recordings through Whisper transcription, publishes events via Redis Streams, and serves results through a web dashboard.

**Features**
- Drag-and-drop upload or file-drop pipeline with live progress dashboard
- Whisper transcription → optional LLM correction → speaker diarization → summarization → chapter detection
- SRT transcript viewer with **audio playback** — click any line to jump to that moment, auto-scroll highlight as audio plays
- **Analytics panel**: aggregate counts, per-speaker time breakdown, keyword trends across all recordings
- REST API for automation and AI agent integration

## Architecture

```
[Host — native, GPU-bound]
  pipeline watcher  (watchdog + FFmpeg + Whisper API + Ollama API)
      ↓ Redis Streams (appendfsync always)
[Docker Compose]
  redis   — MQ + persistence
  api     — FastAPI, event consumer, REST
  web     — dashboard + SRT browser
```

Pipeline core stays on host because Whisper (mlx-whisper) and Ollama require Apple Silicon / GPU.
API + Web are containerized and can be deployed remotely.

## Quick Start

```bash
# 1. Services (Redis + API + Web)
cp config.yaml.example config.yaml   # edit as needed
bash scripts/start-services.sh

# 2. Pipeline watcher (host-native)
bash scripts/start-pipeline.sh

# 3. Drop files into workspace/1_input/
```

Web UI: http://localhost:3000
API:    http://localhost:8080

## Requirements

- Docker + Docker Compose
- Python 3.11+ (for pipeline watcher)
- Whisper service running on port 9001
- Ollama running on port 11434

## Project Structure

```
pipeline/   — host-native watcher + MQ publisher
api/        — FastAPI (containerized)
web/        — Jinja2 frontend (containerized)
scripts/    — start-pipeline.sh, start-services.sh
```

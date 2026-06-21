# Release Tracker

See [`docs/git-workflow.md`](git-workflow.md) for tagging procedure.

---

## Next Release — v2.0.0 (unreleased)

**Theme:** v2 architecture — MinIO-native pipeline, DAG-Service, Progress Worker

| Area | Change |
|------|--------|
| pipeline/worker.py | New standalone Progress Worker process; reads `mediaflow:jobs` MQ; ack-immediately pattern |
| api/services/dag.py | DAG-Service: `trigger_job`, `handle_stage_callback`, `recover_stuck_jobs` (watchdog) |
| api/services/project.py | Project Service: `on_upload_trigger`, FR6 validation, capacity check (429) |
| api/routes/dag_callback.py | `POST /internal/stage-callback` — worker reports per-stage results |
| api/routes/clip.py | `GET /jobs/{id}/segment/{index}/audio` — on-demand MinIO clip with 1h presigned URL |
| api/routes/correction.py | `PATCH /jobs/{id}/correction`, `POST .../finalize` — FR4 transcript correction |
| api/routes/jobs.py | `DELETE /jobs/{id}`, `POST /jobs/{id}/rerun`, `GET /jobs/{id}/events` |
| pipeline/watcher.py | Uploads to MinIO `input/`; POSTs to `/jobs` (no longer runs pipeline locally) |
| pipeline/runner.py | `per_stage_done` hook for intermediate persistence |
| pipeline/worker.py | Intermediate persistence: `preprocess` → `clean.wav`, `transcribe` → `.srt` + `_segments.json` to `processing/{job}/intermediates/`; restore on mid-stage resume |
| api/utils/minio.py | 4-bucket model: input / processing / output / clips |
| DELETED | `api/mq/events_consumer.py`, `api/mq/jobs_consumer.py`, `api/services/event_processor.py`, `api/routes/events.py`, `api/routes/tasks.py`, `pipeline/mq/publisher.py` |
| docs/architecture.md | Full design reference (API, DB schema, DAG flows, MinIO TTLs, retry model) |
| docs/git-workflow.md | This workflow guide |
| CLAUDE.md | Rewritten for v2; progressive disclosure to docs/ |

**Pre-release checklist:**
- [ ] `pytest tests/ -q --ignore=tests/web` — 98 passed, 0 failed
- [ ] Smoke test: `bash tests/run-pipeline-test.sh`
- [ ] Worker runs correctly with `python -m pipeline.worker`
- [ ] Watcher picks up a dropped file and triggers the full pipeline
- [ ] API health: `curl localhost:8080/health`
- [ ] Review `docs/architecture.md` is accurate

---

## Release History

| Version | Date | Theme | Notable changes |
|---------|------|-------|-----------------|
| [v1.2.0](#v120) | 2026-06-21 | Editor UX | Shift+click range select, bulk delete, resizable panels, SrtEditor seek |
| [v1.1.0](#v110) | 2026-06-?? | Frontend panels | RightPanel (audio player, segment list, edit), SummarySection, KeywordList |
| [v1.0.0](#v100) | 2026-06-?? | Stable baseline | First production-ready release |
| [v0.3.0](#v030) | — | — | — |
| [v0.2.2](#v022) | — | — | — |
| [v0.2.1](#v021) | — | — | — |
| [v0.2.0](#v020) | — | — | — |

---

## v1.2.0

**2026-06-21** — Editor UX, resizable panels, bulk delete

- `feat(frontend)`: shift+click range select in transcript list
- `feat(frontend)`: bulk delete in transcript list
- `feat(frontend)`: resizable panels, tab layout, SrtEditor seek
- `refactor`: dead code purge + initial_prompt upload setting
- `feat(plugins)`: installed_plugins and settings for ponytail plugin
- `fix(whisper)`: sanitize NaN/Inf floats before JSON serialisation
- `fix(api,frontend,whisper)`: delete transcript, inline confirm, fix Whisper beam_size

---

## v1.1.0

Frontend panels milestone.

- RightPanel: audio player, title bar, segment list, inline edit
- SummarySection + KeywordList: lazy-fetched collapsible sections
- Frontend refactor: remove dead API methods, slim SpeakerData
- `fix(api,pipeline)`: unblock upload queue — fix active-slot count and watchdog on macOS
- `fix(web)`: nginx dynamic DNS resolver for api upstream

---

## v1.0.0

First production-ready release. Details TBD.

---

## v0.3.0 and earlier

Pre-React frontend era. Details TBD.

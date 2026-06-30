# Release Tracker

See [`docs/git-workflow.md`](git-workflow.md) for tagging procedure.

---

## Next Release — v2.1.1 (unreleased)

**Theme:** MLX-native LLM provider, vad_trim stage, correct_srt stage

| Area | Change |
|------|--------|
| pipeline/providers/llm.py | `LLMProvider` abstraction; factory selects MLX or Ollama |
| pipeline/providers/llm_mlx.py | `MLXLLMProvider`: lazy-load per job, unload + Metal cache clear after |
| pipeline/stages.py | `vad_trim` stage (silence removal before transcribe); `correct_srt` stage (LLM post-correction) |
| api/db/migrations/006_general_v3.sql | `general-v3` dag_flow: preprocess → vad_trim → transcribe → correct_srt → summarize |
| pipeline/stages.py | `from __future__ import annotations` — fix Python 3.9 runtime error on union type hints |
| pipeline/stages.py | `summarize()`: fix `model` variable undefined after LLMProvider refactor (md + JSON output) |
| frontend/src/api/client.ts | `rerunTask`: pass `null` body to `json()` (3-arg signature) |

**Pre-release checklist:**
- [ ] Smoke test: `bash tests/run-pipeline-test.sh`
- [ ] Worker runs correctly with `python -m pipeline.worker`
- [ ] Watcher picks up a dropped file and triggers the full pipeline
- [ ] API health: `curl localhost:8080/health`
- [ ] Review `docs/architecture.md` is accurate

---

## Release History

| Version | Date | Theme | Notable changes |
|---------|------|-------|-----------------|
| [v2.1.1](#v211) | 2026-06-30 | Bug fixes | Python 3.9 compat, summarize model var, rerunTask body |
| [v2.1.0](#v210) | 2026-06-30 | MLX LLM provider | LLMProvider abstraction, vad_trim, correct_srt, general-v3 dag |
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

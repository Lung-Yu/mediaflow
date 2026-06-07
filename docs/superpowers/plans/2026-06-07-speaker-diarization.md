# Speaker Diarization (Local) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `diarize` pipeline stage that identifies speakers, enriches the SRT with relative labels (`【SPEAKER_00】text`), and writes `{stem}_diarization.json`. All inference runs locally, no cloud calls, no HuggingFace token required.

**Architecture:** Local FastAPI HTTP service (`localhost:9003`) in a separate venv. Service uses speechbrain ECAPA-TDNN (Apache 2.0) for speaker embeddings + sklearn clustering. Segmentation uses Whisper's existing `{stem}_segments.json` instead of a separate VAD — more accurate, no extra dependency.

**Tech Stack:** speechbrain ≥ 1.0 (Apache 2.0), scikit-learn (BSD), soundfile, FastAPI/uvicorn, httpx (already in requirements.txt)

**Licensing:** speechbrain models are Apache 2.0, publicly downloadable without HuggingFace token or user agreement — safe for future commercial use.

**Speaker identity scope (Phase 1):** Relative labels only (SPEAKER_00, SPEAKER_01). Enrollment / voiceprint library is Phase 2 (separate plan).

---

## Licensing notes

| Component | License | Token needed? |
|-----------|---------|---------------|
| speechbrain library | Apache 2.0 | No |
| `speechbrain/spkrec-ecapa-voxceleb` model | Apache 2.0 | No |
| scikit-learn | BSD | No |
| pyannote.audio library (MIT) + `pyannote/speaker-diarization-3.1` model | **Restricted: non-commercial only** | Yes — **do not use** |

---

## File Map

**Create:**
| File | Purpose |
|------|---------|
| `diarize/__init__.py` | Package marker |
| `diarize/service.py` | FastAPI HTTP service; speechbrain ECAPA-TDNN + sklearn clustering |
| `diarize/requirements.txt` | speechbrain + scikit-learn + soundfile + fastapi (separate venv) |
| `scripts/start-diarize.sh` | Start service; creates `venv-diarize/` on first run |
| `tests/test_stages.py` | Unit tests for `_assign_speaker` and `diarize()` |

**Modify:**
| File | Change |
|------|--------|
| `pipeline/stages.py` | Add `_assign_speaker()` helper + `diarize()` stage |
| `pipeline/runner.py` | Add `_adapt_diarize`, insert into `STAGE_RUNNERS` + `_DEFAULT_STAGES` |
| `requirements.txt` | Add `pytest` for unit tests |
| `config.yaml.example` | Add `diarize` stage entry + `diarization:` config section |
| `.gitignore` | Add `venv-diarize/` |
| `CLAUDE.md` | Key files, API docs, implementation status |

**Stage order after this plan:**
```
preprocess → transcribe → verify_segments → correct_srt → diarize → summarize → detect_chapters
```

---

## How the service works

```
POST /diarize
  audio:    WAV file (clean 16kHz mono from 2_processing/)
  segments: JSON string of [{start, end}, ...] from {stem}_segments.json (optional)

For each segment:
  1. FFmpeg clip the audio to [start, end]
  2. ECAPA-TDNN → 192-dim speaker embedding
  3. Collect all embeddings

Cluster embeddings:
  - if num_speakers set in config: AgglomerativeClustering(n_clusters=N)
  - else: AgglomerativeClustering(distance_threshold=0.4, metric='cosine')

Return:
  {"segments": [{"speaker": "SPEAKER_00", "start": 0.5, "end": 3.2}, ...]}
```

When `segments` is passed (Whisper-guided), the service skips its own VAD — more accurate segmentation since Whisper already found speech boundaries.

---

## Task 1: Diarization HTTP service

**Files:**
- Create: `diarize/__init__.py`
- Create: `diarize/service.py`
- Create: `diarize/requirements.txt`
- Create: `scripts/start-diarize.sh`
- Modify: `.gitignore`

- [ ] **Create `diarize/__init__.py`**

```bash
touch diarize/__init__.py
```

- [ ] **Create `diarize/requirements.txt`**

```
speechbrain>=1.0.0
scikit-learn>=1.4.0
soundfile>=0.12.0
numpy>=1.26.0
fastapi==0.115.0
uvicorn[standard]==0.30.0
```

Note: speechbrain installs torch as a dependency. No HuggingFace token required — the ECAPA-TDNN model is downloaded from speechbrain's CDN on first use and cached in `~/.cache/huggingface/hub/`.

- [ ] **Create `diarize/service.py`**

```python
"""Local speaker diarization HTTP service.

Uses speechbrain ECAPA-TDNN (Apache 2.0) for speaker embeddings
and sklearn AgglomerativeClustering. No HuggingFace token required.

Segmentation strategy:
  - If 'segments' param provided: use caller's timestamps (e.g. from Whisper)
  - Models downloaded to ~/.cache/huggingface/hub/ on first request (~200 MB).

Usage:
  uvicorn diarize.service:app --port 9003
"""
import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from sklearn.cluster import AgglomerativeClustering
from sklearn.preprocessing import normalize

app = FastAPI()

_encoder = None


def _get_encoder():
    global _encoder
    if _encoder is not None:
        return _encoder
    from speechbrain.inference.speaker import EncoderClassifier
    _encoder = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir="pretrained_models/spkrec-ecapa-voxceleb",
        run_opts={"device": _best_device()},
    )
    return _encoder


def _best_device() -> str:
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _embed_clip(encoder, wav_path: Path) -> "np.ndarray | None":
    """Return ECAPA-TDNN embedding for a WAV clip, or None on failure."""
    try:
        import torch
        signal, sr = sf.read(str(wav_path))
        if signal.ndim > 1:
            signal = signal.mean(axis=1)
        if len(signal) < sr * 0.1:  # skip clips shorter than 100 ms
            return None
        tensor = torch.tensor(signal, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            embedding = encoder.encode_batch(tensor)
        return embedding.squeeze().cpu().numpy()
    except Exception:
        return None


def _cluster(embeddings: list, num_speakers: int | None, threshold: float) -> list:
    """Cluster embeddings; returns list of integer speaker labels."""
    if len(embeddings) == 1:
        return [0]
    X = normalize(np.array(embeddings))
    if num_speakers:
        model = AgglomerativeClustering(
            n_clusters=num_speakers,
            metric="cosine",
            linkage="average",
        )
    else:
        model = AgglomerativeClustering(
            n_clusters=None,
            distance_threshold=threshold,
            metric="cosine",
            linkage="average",
        )
    return model.fit_predict(X).tolist()


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": _encoder is not None}


@app.post("/diarize")
async def diarize(
    audio: UploadFile = File(...),
    segments: str = Form(None),       # JSON: [{start, end}, ...]
    num_speakers: int = Query(None),
    cluster_threshold: float = Query(0.4),
):
    encoder = _get_encoder()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(await audio.read())
        wav_path = Path(tmp.name)

    try:
        # Parse or detect segments
        if segments:
            segs = json.loads(segments)
        else:
            # Fallback: treat the whole file as one segment
            import soundfile as sf_check
            info = sf_check.info(str(wav_path))
            segs = [{"start": 0.0, "end": info.duration}]

        # Extract one embedding per segment
        embeddings = []
        valid_idx = []
        clip_path = wav_path.parent / "diarize_clip.wav"

        for i, seg in enumerate(segs):
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav_path),
                 "-ss", str(seg["start"]), "-to", str(seg["end"]),
                 "-ar", "16000", "-ac", "1", str(clip_path)],
                check=True, capture_output=True, timeout=30,
            )
            emb = _embed_clip(encoder, clip_path)
            if emb is not None:
                embeddings.append(emb)
                valid_idx.append(i)

        clip_path.unlink(missing_ok=True)

        if not embeddings:
            return JSONResponse({"segments": []})

        labels = _cluster(embeddings, num_speakers, cluster_threshold)

        result = []
        for label_idx, seg_idx in enumerate(valid_idx):
            seg = segs[seg_idx]
            result.append({
                "speaker": f"SPEAKER_{labels[label_idx]:02d}",
                "start": seg["start"],
                "end": seg["end"],
            })

        return JSONResponse({"segments": result})

    finally:
        wav_path.unlink(missing_ok=True)
```

- [ ] **Create `scripts/start-diarize.sh`**

```bash
#!/usr/bin/env bash
# Start local speaker diarization service on :9003.
# No HuggingFace token needed — speechbrain models are Apache 2.0.
# Creates venv-diarize/ on first run (~200 MB model download).
set -euo pipefail
cd "$(dirname "$0")/.."

VENV="venv-diarize"
if [[ ! -d "$VENV" ]]; then
    echo "Creating $VENV and installing dependencies..."
    python3 -m venv "$VENV"
    source "$VENV/bin/activate"
    pip install --quiet -r diarize/requirements.txt
else
    source "$VENV/bin/activate"
fi

echo "Starting diarization service on :9003 ..."
echo "  First request downloads ECAPA-TDNN model (~200 MB, cached after that)"
exec uvicorn diarize.service:app --host 0.0.0.0 --port 9003
```

```bash
chmod +x scripts/start-diarize.sh
```

- [ ] **Add `venv-diarize/` to `.gitignore`** (after the `venv/` line)

```
venv-diarize/
```

- [ ] **Commit**

```bash
git add diarize/ scripts/start-diarize.sh .gitignore
git commit -m "feat(diarize): local speechbrain ECAPA-TDNN service on :9003 (Apache 2.0)"
```

---

## Task 2: pytest + `_assign_speaker` helper with tests

**Files:**
- Modify: `requirements.txt`
- Create: `tests/test_stages.py`
- Modify: `pipeline/stages.py`

- [ ] **Add pytest to `requirements.txt`**

```
# Dev
pytest==8.3.4
```

- [ ] **Install pytest**

```bash
source venv/bin/activate
pip install pytest==8.3.4
```

- [ ] **Write failing tests in `tests/test_stages.py`**

```python
import pytest
from pipeline.stages import _assign_speaker


def test_assign_speaker_returns_dominant_speaker():
    segs = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 3.0},
        {"speaker": "SPEAKER_01", "start": 3.0, "end": 6.0},
    ]
    assert _assign_speaker(0.5, 2.5, segs) == "SPEAKER_00"
    assert _assign_speaker(3.5, 5.5, segs) == "SPEAKER_01"


def test_assign_speaker_picks_largest_overlap():
    segs = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0},
        {"speaker": "SPEAKER_01", "start": 2.0, "end": 5.0},
    ]
    # block 1.0–4.0: 1 s with SPEAKER_00, 2 s with SPEAKER_01
    assert _assign_speaker(1.0, 4.0, segs) == "SPEAKER_01"


def test_assign_speaker_returns_none_when_no_overlap():
    segs = [{"speaker": "SPEAKER_00", "start": 5.0, "end": 8.0}]
    assert _assign_speaker(0.0, 2.0, segs) is None


def test_assign_speaker_exact_boundary():
    segs = [
        {"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0},
        {"speaker": "SPEAKER_01", "start": 2.0, "end": 4.0},
    ]
    assert _assign_speaker(2.0, 3.0, segs) == "SPEAKER_01"
```

- [ ] **Run tests to verify they fail**

```bash
source venv/bin/activate
pytest tests/test_stages.py -v
# Expected: FAIL — ImportError: cannot import name '_assign_speaker'
```

- [ ] **Add `_assign_speaker` to `pipeline/stages.py`**

Insert after `verify_segments()`, before the `# ── Stage 3: Summarization` comment:

```python
def _assign_speaker(block_start: float, block_end: float, diar_segs: list) -> "str | None":
    """Return the diarization speaker with maximum overlap in [block_start, block_end]."""
    max_overlap = 0.0
    assigned = None
    for seg in diar_segs:
        overlap = min(block_end, seg["end"]) - max(block_start, seg["start"])
        if overlap > max_overlap:
            max_overlap = overlap
            assigned = seg["speaker"]
    return assigned
```

- [ ] **Run tests to verify they pass**

```bash
pytest tests/test_stages.py -v
# Expected: 4 PASSED
```

- [ ] **Commit**

```bash
git add requirements.txt tests/test_stages.py pipeline/stages.py
git commit -m "test: add pytest + _assign_speaker unit tests"
```

---

## Task 3: `stages.diarize()` + tests

**Files:**
- Modify: `pipeline/stages.py`
- Modify: `tests/test_stages.py`

- [ ] **Add diarize tests to `tests/test_stages.py`**

```python
import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from pipeline.stages import diarize


def _make_srt(blocks):
    """blocks: list of (start_hms, end_hms, text) — times as HH:MM:SS"""
    lines = []
    for i, (start, end, text) in enumerate(blocks, start=1):
        lines.append(f"{i}\n{start},000 --> {end},000\n{text}\n")
    return "\n".join(lines)


def test_diarize_enriches_srt_with_speaker_labels():
    cfg = {
        "diarization": {
            "service_url": "http://localhost:9003",
            "speaker_format": "【{speaker}】",
        }
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "segments": [
            {"speaker": "SPEAKER_00", "start": 0.0, "end": 5.0},
            {"speaker": "SPEAKER_01", "start": 5.0, "end": 10.0},
        ]
    }
    mock_resp.raise_for_status = MagicMock()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        srt_path = tmp / "test.srt"
        audio_path = tmp / "test_clean.wav"
        audio_path.write_bytes(b"RIFF" + b"\x00" * 36)
        srt_path.write_text(
            _make_srt([("00:00:01", "00:00:03", "Hello"), ("00:00:06", "00:00:08", "World")]),
            encoding="utf-8",
        )

        with patch("pipeline.stages.httpx.post", return_value=mock_resp):
            result = diarize("test", srt_path, audio_path, cfg)

        srt_out = srt_path.read_text(encoding="utf-8")
        assert "【SPEAKER_00】Hello" in srt_out
        assert "【SPEAKER_01】World" in srt_out
        assert result == tmp / "test_diarization.json"
        assert len(json.loads(result.read_text())) == 2


def test_diarize_passes_whisper_segments_to_service():
    """Service receives segment timestamps from {stem}_segments.json when available."""
    cfg = {"diarization": {"service_url": "http://localhost:9003"}}
    mock_resp = MagicMock()
    mock_resp.json.return_value = {"segments": []}
    mock_resp.raise_for_status = MagicMock()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        srt_path = tmp / "test.srt"
        audio_path = tmp / "test_clean.wav"
        audio_path.write_bytes(b"RIFF" + b"\x00" * 36)
        srt_path.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello\n", encoding="utf-8")
        seg_json = tmp / "test_segments.json"
        seg_json.write_text(
            json.dumps([{"start": 1.0, "end": 3.0, "text": "Hello"}]),
            encoding="utf-8",
        )

        with patch("pipeline.stages.httpx.post", return_value=mock_resp) as mock_post:
            diarize("test", srt_path, audio_path, cfg)

        call_data = mock_post.call_args
        assert "data" in call_data.kwargs
        sent_segs = json.loads(call_data.kwargs["data"]["segments"])
        assert sent_segs == [{"start": 1.0, "end": 3.0}]


def test_diarize_skips_gracefully_when_service_unavailable():
    import httpx as real_httpx

    cfg = {"diarization": {"service_url": "http://localhost:9003"}}

    with tempfile.TemporaryDirectory() as tmpdir:
        srt_path = Path(tmpdir) / "test.srt"
        audio_path = Path(tmpdir) / "test_clean.wav"
        audio_path.write_bytes(b"RIFF" + b"\x00" * 36)
        original = "1\n00:00:01,000 --> 00:00:03,000\nHello\n"
        srt_path.write_text(original, encoding="utf-8")

        with patch("pipeline.stages.httpx.post", side_effect=real_httpx.ConnectError("refused")):
            diarize("test", srt_path, audio_path, cfg)

        assert srt_path.read_text(encoding="utf-8") == original


def test_diarize_applies_speaker_name_mapping():
    cfg = {
        "diarization": {
            "service_url": "http://localhost:9003",
            "speaker_format": "【{speaker}】",
            "speaker_names": {"SPEAKER_00": "老師"},
        }
    }
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "segments": [{"speaker": "SPEAKER_00", "start": 0.0, "end": 5.0}]
    }
    mock_resp.raise_for_status = MagicMock()

    with tempfile.TemporaryDirectory() as tmpdir:
        srt_path = Path(tmpdir) / "test.srt"
        audio_path = Path(tmpdir) / "test_clean.wav"
        audio_path.write_bytes(b"RIFF" + b"\x00" * 36)
        srt_path.write_text("1\n00:00:01,000 --> 00:00:03,000\nHello\n", encoding="utf-8")

        with patch("pipeline.stages.httpx.post", return_value=mock_resp):
            diarize("test", srt_path, audio_path, cfg)

        assert "【老師】Hello" in srt_path.read_text(encoding="utf-8")


def test_diarize_skips_when_audio_missing():
    cfg = {"diarization": {"service_url": "http://localhost:9003"}}

    with tempfile.TemporaryDirectory() as tmpdir:
        srt_path = Path(tmpdir) / "test.srt"
        audio_path = Path(tmpdir) / "nonexistent.wav"
        original = "1\n00:00:01,000 --> 00:00:03,000\nHello\n"
        srt_path.write_text(original, encoding="utf-8")

        with patch("pipeline.stages.httpx.post") as mock_post:
            diarize("test", srt_path, audio_path, cfg)
            mock_post.assert_not_called()

        assert srt_path.read_text(encoding="utf-8") == original
```

- [ ] **Run tests to verify they fail**

```bash
pytest tests/test_stages.py::test_diarize_enriches_srt_with_speaker_labels -v
# Expected: FAIL — ImportError: cannot import name 'diarize' from 'pipeline.stages'
```

- [ ] **Add `diarize()` to `pipeline/stages.py`**

Insert after `_assign_speaker()`, before `# ── Stage 3: Summarization`:

```python
def diarize(stem: str, srt_path: Path, audio_path: Path, cfg: dict) -> Path:
    """Diarize audio via local service and enrich SRT with speaker labels.

    Passes Whisper segment timestamps to the service when {stem}_segments.json
    exists — avoids redundant VAD and uses Whisper's more accurate boundaries.
    Saves {stem}_diarization.json. Never raises.
    """
    diar_path = srt_path.parent / f"{stem}_diarization.json"
    d_cfg = cfg.get("diarization", {})
    service_url = d_cfg.get("service_url", "http://localhost:9003").rstrip("/")
    speaker_fmt = d_cfg.get("speaker_format", "【{speaker}】")
    speaker_names = d_cfg.get("speaker_names", {})
    num_speakers = d_cfg.get("num_speakers", None)

    if not audio_path.exists():
        log.warning("diarize: clean WAV not found for %s — skipping", stem)
        return diar_path

    # Pass Whisper segment timestamps when available (avoids redundant VAD)
    seg_path = srt_path.parent / f"{stem}_segments.json"
    whisper_segs = None
    if seg_path.exists():
        raw = json.loads(seg_path.read_text(encoding="utf-8"))
        whisper_segs = [{"start": s["start"], "end": s["end"]} for s in raw]

    try:
        params = {"num_speakers": num_speakers} if num_speakers else {}
        data = {"segments": json.dumps(whisper_segs)} if whisper_segs else {}
        with open(audio_path, "rb") as f:
            resp = httpx.post(
                f"{service_url}/diarize",
                files={"audio": (audio_path.name, f)},
                params=params,
                data=data,
                timeout=600.0,
            )
        resp.raise_for_status()
    except httpx.ConnectError:
        log.warning("diarize: cannot reach service at %s — skipping", service_url)
        return diar_path
    except httpx.HTTPStatusError as exc:
        log.warning("diarize: service error %d — skipping", exc.response.status_code)
        return diar_path

    diar_segs = resp.json().get("segments", [])
    diar_path.write_text(json.dumps(diar_segs, ensure_ascii=False, indent=2), encoding="utf-8")

    if not diar_segs:
        log.info("diarize: no speaker segments returned for %s", stem)
        return diar_path

    srt_content = srt_path.read_text(encoding="utf-8", errors="replace")
    blocks = _parse_srt_blocks(srt_content)
    lines = []
    for i, block in enumerate(blocks):
        raw_speaker = _assign_speaker(
            _start_seconds(block["time"]),
            _end_seconds(block["time"]),
            diar_segs,
        )
        if raw_speaker:
            display = speaker_names.get(raw_speaker, raw_speaker)
            text = speaker_fmt.format(speaker=display) + block["text"]
        else:
            text = block["text"]
        lines.append(f"{i + 1}\n{block['time']}\n{text}\n")
    srt_path.write_text("\n".join(lines), encoding="utf-8")

    log.info("diarize done: %s (%d speaker segments)", stem, len(diar_segs))
    return diar_path
```

- [ ] **Run all tests to verify they pass**

```bash
pytest tests/test_stages.py -v
# Expected: 9 PASSED (4 _assign_speaker + 5 diarize)
```

- [ ] **Commit**

```bash
git add pipeline/stages.py tests/test_stages.py
git commit -m "feat(stages): diarize() with Whisper-guided segmentation + tests"
```

---

## Task 4: Runner wiring

**Files:**
- Modify: `pipeline/runner.py`

- [ ] **Add `_adapt_diarize` adapter** — insert after `_adapt_correct_srt`, before `_adapt_summarize`

```python
def _adapt_diarize(ctx: dict, cfg: dict) -> tuple[dict, dict]:
    srt_path = ctx["srt_path"]
    audio_path = ctx["audio_path"]
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT not found: {srt_path}")
    if not audio_path.exists():
        log.warning("diarize skipped: audio_path not found (%s)", audio_path)
        return ctx, {}
    stages.diarize(ctx["stem"], srt_path, audio_path, cfg)
    return ctx, {}
```

- [ ] **Update `STAGE_RUNNERS`**

```python
STAGE_RUNNERS: dict[str, Callable] = {
    "preprocess":      _adapt_preprocess,
    "transcribe":      _adapt_transcribe,
    "verify_segments": _adapt_verify_segments,
    "correct_srt":     _adapt_correct_srt,
    "diarize":         _adapt_diarize,
    "summarize":       _adapt_summarize,
    "detect_chapters": _adapt_detect_chapters,
}
```

- [ ] **Update `_DEFAULT_STAGES`**

```python
_DEFAULT_STAGES = [
    {"id": "preprocess",       "enabled": True},
    {"id": "transcribe",       "enabled": True},
    {"id": "verify_segments",  "enabled": False},
    {"id": "correct_srt",      "enabled": False},
    {"id": "diarize",          "enabled": False},
    {"id": "summarize",        "enabled": True},
    {"id": "detect_chapters",  "enabled": False},
]
```

- [ ] **Verify imports and stage list**

```bash
source venv/bin/activate
python -c "
from pipeline import runner
print(list(runner.STAGE_RUNNERS.keys()))
assert 'diarize' in runner.STAGE_RUNNERS
"
# Expected: ['preprocess', 'transcribe', 'verify_segments', 'correct_srt', 'diarize', 'summarize', 'detect_chapters']
```

- [ ] **Run full test suite**

```bash
pytest tests/test_stages.py -v
# Expected: 9 PASSED
```

- [ ] **Commit**

```bash
git add pipeline/runner.py
git commit -m "feat(runner): wire diarize into stage pipeline"
```

---

## Task 5: Config + docs

**Files:**
- Modify: `config.yaml.example`
- Modify: `CLAUDE.md`

- [ ] **Add `diarize` stage to `config.yaml.example`**

In `pipeline.stages`, insert after the `correct_srt` entry:

```yaml
    - id: diarize
      enabled: false               # speaker diarization via speechbrain ECAPA-TDNN :9003
```

- [ ] **Add `diarization:` config section to `config.yaml.example`**

Insert after the `chapters:` section:

```yaml
diarization:
  service_url: http://localhost:9003
  num_speakers: null               # set to known count for better accuracy (e.g. 2)
  speaker_format: "【{speaker}】"   # prefix format; use "" to disable labels in SRT
  speaker_names: {}                # display name mapping e.g. {SPEAKER_00: 老師, SPEAKER_01: 學生}
  cluster_threshold: 0.4          # cosine distance threshold for auto speaker count
```

- [ ] **Update `CLAUDE.md` Key Files section**

In the Key Files section, add after the pipeline block:

```
diarize/
  service.py          — FastAPI diarization service on :9003; speechbrain ECAPA-TDNN + sklearn
  requirements.txt    — Apache 2.0 deps only; no HuggingFace token required (separate venv)
```

- [ ] **Add diarization API docs to `CLAUDE.md`** (in External Service APIs section)

```markdown
### Diarization Service (`pipeline/stages.py: diarize()`)

**No HuggingFace token needed.** speechbrain ECAPA-TDNN model (~200 MB) downloads
on first request and caches in `~/.cache/huggingface/hub/`. Apache 2.0 licensed.

Start: `bash scripts/start-diarize.sh`

```python
# POST /diarize
httpx.post(
    "http://localhost:9003/diarize",
    files={"audio": (audio_path.name, file_handle)},
    data={"segments": json.dumps([{"start": 1.0, "end": 3.5}, ...])},
    params={"num_speakers": 2},   # optional
    timeout=600.0,
)
# Response: {"segments": [{"speaker": "SPEAKER_00", "start": 1.0, "end": 3.5}, ...]}
```

When `segments` is passed (from `{stem}_segments.json`), uses Whisper's boundaries
instead of running its own VAD. More accurate; avoids redundant segmentation.
```

- [ ] **Update Implementation Status in `CLAUDE.md`**

Add to ✅ Done table:

```markdown
| P4-3 | Speaker diarization (speechbrain, Apache 2.0) | `diarize/service.py`, `pipeline/stages.py` (`diarize`) |
```

Remove the speaker diarization bullet from ❌ Not Yet Implemented.

- [ ] **Commit**

```bash
git add config.yaml.example CLAUDE.md
git commit -m "docs: diarization config, service API, licensing notes"
```

---

## Notes for the implementing agent

**Python 3.9 compatibility:** Do not use `X | Y` union syntax. Use `"str | None"` as string annotation or `Optional[str]` with `from typing import Optional`.

**`_parse_srt_blocks`, `_start_seconds`, `_end_seconds`** are defined later in `pipeline/stages.py` (summarization section). Python resolves these at call time, not parse time — no forward-reference issue.

**`venv-diarize/` is separate** from `venv/`. speechbrain's torch dependency is heavy (~2 GB) and should not pollute the pipeline environment.

**Cold start:** ECAPA-TDNN model downloads ~200 MB on first request and loads into MPS. Subsequent requests within the same process are fast.

**`cluster_threshold` tuning:** Lower value (e.g. 0.3) → more speakers detected. Higher (e.g. 0.5) → fewer speakers. Default 0.4 works for 2–3 speaker recordings. Set `num_speakers` when known.

**Phase 2 — enrollment (future plan):**
- Pre-record reference clips per speaker: `workspace/speaker_profiles/{name}.wav`
- Extract ECAPA-TDNN embedding, store in `data/speaker_library.json`
- After diarization: compare each cluster embedding against library
- If cosine similarity > match_threshold → assign known name; else → UNKNOWN_X
- `pipeline.enroll` CLI: `python -m pipeline.enroll --name 老師 --audio sample.wav`

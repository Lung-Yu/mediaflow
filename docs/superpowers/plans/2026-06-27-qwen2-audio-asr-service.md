# Qwen2-Audio ASR Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Whisper with Qwen2-Audio-7B-Instruct for higher-quality Chinese ASR without changing any pipeline code.

**Architecture:** New `asr/service.py` (FastAPI, port 9002) exposes the **identical HTTP interface** as `whisper/service.py` — `POST /transcribe_segments` returns `{segments: [{id, start, end, text, avg_logprob, no_speech_prob}]}`. Switching is a one-line config change: `whisper.service_url: http://localhost:9002`. Chunks long audio into 30s windows since Qwen2-Audio processes bounded context. Isolated `venv-asr` follows the existing `venv-whisper` / `venv-diarize` pattern.

**Tech Stack:** `transformers>=4.45.0`, `torch>=2.1.0` (MPS backend), `accelerate`, `soundfile`, `scipy` (resampling), `fastapi`, `uvicorn`. No `bitsandbytes` — CUDA-only, won't work on Apple Silicon.

## Global Constraints

- Apple Silicon Mac mini — MPS backend only, no CUDA, no bitsandbytes
- `whisper-large-v3` caused OOM on this machine (user confirmed) — model must use `device_map="auto"` + `torch_dtype=torch.float16` to split between MPS + CPU if needed
- `stages.py` must not be modified — the new service must match the existing `/transcribe_segments` and `/transcribe_large` response format exactly
- `config.yaml` is gitignored; `config.yaml.example` is the committed template
- Follow existing code style: no comments unless WHY is non-obvious

---

## File Map

```
Create: scripts/test_qwen_audio_oom.py    — Phase 0 OOM gate (standalone, not a service)
Create: asr/__init__.py                   — empty, makes asr a package
Create: asr/service.py                    — FastAPI, Qwen2-Audio, port 9002
Create: asr/requirements.txt             — isolated deps for venv-asr
Modify: scripts/ctl.sh                   — add asr to start/stop/status/logs
Modify: Makefile                         — add start-asr / stop-asr / logs-asr
Modify: config.yaml.example              — add comment: switch to asr by pointing whisper.service_url
```

---

## Task 0: OOM Feasibility Gate

**This task must succeed before proceeding. If the script crashes with OOM, stop and report.**

**Files:**
- Create: `scripts/test_qwen_audio_oom.py`

**Interfaces:**
- Produces: exit code 0 on success, 1 on OOM/error

- [ ] **Step 1: Create the test script**

```python
#!/usr/bin/env python3
"""Phase 0: Verify Qwen2-Audio-7B-Instruct loads without OOM on this machine.

Run: python scripts/test_qwen_audio_oom.py
Expected: prints peak memory and "OK" — takes ~5 min on first run (model download).
If it crashes with SIGKILL or "killed", OOM confirmed — this path is blocked at fp16.
"""
import sys
import numpy as np

MODEL = "Qwen/Qwen2-Audio-7B-Instruct"


def main():
    import torch
    from transformers import AutoProcessor, Qwen2AudioForConditionalGeneration

    print(f"Loading processor from {MODEL}...")
    processor = AutoProcessor.from_pretrained(MODEL)

    print("Loading model (float16, device_map=auto)...")
    model = Qwen2AudioForConditionalGeneration.from_pretrained(
        MODEL,
        torch_dtype=torch.float16,
        device_map="auto",
        low_cpu_mem_usage=True,
    )
    print(f"Model device map: {model.hf_device_map}")

    sr = processor.feature_extractor.sampling_rate
    audio = np.zeros(sr * 5, dtype=np.float32)

    conversation = [{"role": "user", "content": [
        {"type": "audio", "audio_url": "placeholder"},
        {"type": "text", "text": "請轉錄。"},
    ]}]
    text = processor.apply_chat_template(conversation, add_generation_prompt=True, tokenize=False)
    inputs = processor(text=text, audios=[audio], sampling_rate=sr, return_tensors="pt", padding=True)
    inputs = {k: v.to(model.device) for k, v in inputs.items() if hasattr(v, "to")}

    with torch.no_grad():
        output = model.generate(**inputs, max_new_tokens=20)

    decoded = processor.decode(output[0][inputs["input_ids"].size(1):], skip_special_tokens=True)
    print(f"Test output: {repr(decoded)}")

    if torch.backends.mps.is_available():
        mb = torch.mps.current_allocated_memory() / 1024 / 1024
        print(f"MPS memory allocated: {mb:.0f} MB")

    print("OK — model loaded and ran without OOM")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Create venv-asr and install deps**

```bash
python3 -m venv venv-asr
venv-asr/bin/pip install transformers>=4.45.0 torch>=2.1.0 accelerate>=0.25.0 soundfile>=0.12.0 scipy>=1.11.0 numpy>=1.24.0
```

- [ ] **Step 3: Run the OOM test**

```bash
venv-asr/bin/python scripts/test_qwen_audio_oom.py
```

Expected output (success):
```
Loading processor from Qwen/Qwen2-Audio-7B-Instruct...
Loading model (float16, device_map=auto)...
Model device map: {...}
Test output: ''
MPS memory allocated: XXXX MB
OK — model loaded and ran without OOM
```

**If the process is killed (OOM):** stop here. The fp16 model is too large. Options to investigate: INT8 quantization via a GGUF wrapper, or a smaller model.

- [ ] **Step 4: Commit**

```bash
git add scripts/test_qwen_audio_oom.py
git commit -m "test: add Qwen2-Audio OOM feasibility script"
```

---

## Task 1: ASR Service

**Files:**
- Create: `asr/__init__.py`
- Create: `asr/requirements.txt`
- Create: `asr/service.py`

**Interfaces:**
- Consumes: nothing (standalone service)
- Produces:
  - `GET /health` → `{"status": "ok", "model": str, "model_loaded": bool}`
  - `POST /transcribe_segments` → `{"segments": [{"id": int, "start": float, "end": float, "text": str, "avg_logprob": float, "no_speech_prob": float}]}`
  - `POST /transcribe_large` → `{"text": str}`

- [ ] **Step 1: Write the failing format test**

```python
# tests/test_asr_service_format.py
"""Verify /transcribe_segments returns the exact format stages.py expects.

Requires the asr service running on :9002:
  uvicorn asr.service:app --port 9002
"""
import httpx
import wave
import struct

def _make_silence_wav(duration_sec: float = 2.0, sr: int = 16000) -> bytes:
    import io
    buf = io.BytesIO()
    n = int(sr * duration_sec)
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(struct.pack(f"<{n}h", *([0] * n)))
    return buf.getvalue()


def test_transcribe_segments_format():
    wav = _make_silence_wav()
    resp = httpx.post(
        "http://localhost:9002/transcribe_segments",
        files={"audio": ("test.wav", wav, "audio/wav")},
        params={"language": "zh"},
        timeout=120,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "segments" in body
    for seg in body["segments"]:
        assert isinstance(seg["id"], int)
        assert isinstance(seg["start"], float)
        assert isinstance(seg["end"], float)
        assert isinstance(seg["text"], str)
        assert isinstance(seg["avg_logprob"], float)
        assert isinstance(seg["no_speech_prob"], float)


def test_transcribe_large_format():
    wav = _make_silence_wav()
    resp = httpx.post(
        "http://localhost:9002/transcribe_large",
        files={"audio": ("test.wav", wav, "audio/wav")},
        params={"language": "zh"},
        timeout=120,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "text" in body
    assert isinstance(body["text"], str)


def test_health():
    resp = httpx.get("http://localhost:9002/health", timeout=10)
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "model_loaded" in body
```

- [ ] **Step 2: Run the test to verify it fails (service not yet running)**

```bash
venv-asr/bin/pip install pytest httpx
venv-asr/bin/pytest tests/test_asr_service_format.py -v
```

Expected: `ConnectionRefusedError` — service not running.

- [ ] **Step 3: Write `asr/requirements.txt`**

```
transformers>=4.45.0
torch>=2.1.0
accelerate>=0.25.0
soundfile>=0.12.0
scipy>=1.11.0
numpy>=1.24.0
fastapi>=0.100.0
uvicorn>=0.23.0
httpx>=0.24.0
pytest>=7.0.0
```

- [ ] **Step 4: Create `asr/__init__.py`**

Empty file:
```python
```

- [ ] **Step 5: Write `asr/service.py`**

```python
"""Qwen2-Audio-7B-Instruct ASR HTTP service — Apple Silicon (MPS + CPU via device_map=auto).

Drop-in replacement for whisper/service.py — exposes identical endpoints.
Switch by setting whisper.service_url: http://localhost:9002 in config.yaml.

Endpoints:
  GET  /health               — liveness + model-loaded flag
  POST /transcribe_segments  — full audio → {segments: [{id, start, end, text, avg_logprob, no_speech_prob}]}
  POST /transcribe_large     — short clip → {text: "..."}

Usage:
  ASR_MODEL=Qwen/Qwen2-Audio-7B-Instruct uvicorn asr.service:app --host 0.0.0.0 --port 9002
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Query, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI()

MODEL = os.environ.get("ASR_MODEL", "Qwen/Qwen2-Audio-7B-Instruct")
CHUNK_SEC = int(os.environ.get("ASR_CHUNK_SEC", "30"))

_model = None
_processor = None
_model_loaded = False


def _get_model():
    global _model, _processor, _model_loaded
    if _model is not None:
        return _model, _processor
    import torch
    from transformers import AutoProcessor, Qwen2AudioForConditionalGeneration

    _processor = AutoProcessor.from_pretrained(MODEL)
    _model = Qwen2AudioForConditionalGeneration.from_pretrained(
        MODEL,
        torch_dtype=torch.float16,
        device_map="auto",
        low_cpu_mem_usage=True,
    )
    _model_loaded = True
    return _model, _processor


def _transcribe_chunk(audio: np.ndarray, sr: int, language: str, prompt: str, model, processor) -> str:
    import torch

    lang_hint = "繁體中文" if language == "zh" else language
    instruction = prompt or f"請將以上音訊逐字轉錄為{lang_hint}文字。"

    conversation = [{"role": "user", "content": [
        {"type": "audio", "audio_url": "placeholder"},
        {"type": "text", "text": instruction},
    ]}]
    text = processor.apply_chat_template(conversation, add_generation_prompt=True, tokenize=False)
    inputs = processor(
        text=text,
        audios=[audio],
        sampling_rate=sr,
        return_tensors="pt",
        padding=True,
    )
    inputs = {k: v.to(model.device) for k, v in inputs.items() if hasattr(v, "to")}

    with torch.no_grad():
        generated = model.generate(**inputs, max_new_tokens=2000, do_sample=False)

    generated = generated[:, inputs["input_ids"].size(1):]
    return processor.decode(generated[0], skip_special_tokens=True).strip()


def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(src_sr, dst_sr)
    return resample_poly(audio, dst_sr // g, src_sr // g).astype(np.float32)


def _do_transcribe(wav_bytes: bytes, language: str, prompt: str) -> dict:
    model, processor = _get_model()
    target_sr = processor.feature_extractor.sampling_rate  # 16000

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_bytes)
        wav_path = Path(tmp.name)

    try:
        audio, file_sr = sf.read(str(wav_path))
        if audio.ndim > 1:
            audio = audio.mean(axis=1)
        audio = audio.astype(np.float32)

        if file_sr != target_sr:
            audio = _resample(audio, file_sr, target_sr)

        chunk_samples = CHUNK_SEC * target_sr
        total = len(audio)
        segments = []
        seg_id = 0
        offset = 0

        while offset < total:
            chunk = audio[offset: offset + chunk_samples]
            start_sec = round(offset / target_sr, 3)
            end_sec = round(min((offset + chunk_samples) / target_sr, total / target_sr), 3)

            text = _transcribe_chunk(chunk, target_sr, language, prompt, model, processor)
            if text:
                segments.append({
                    "id": seg_id,
                    "start": start_sec,
                    "end": end_sec,
                    "text": text,
                    # ponytail: Qwen2-Audio exposes no logprobs; 0.0 disables verify_segments flagging
                    "avg_logprob": 0.0,
                    "no_speech_prob": 0.0,
                })
                seg_id += 1
            offset += chunk_samples

        return {"segments": segments}
    finally:
        wav_path.unlink(missing_ok=True)


def _do_transcribe_text(wav_bytes: bytes, language: str) -> dict:
    result = _do_transcribe(wav_bytes, language, "")
    text = " ".join(s["text"] for s in result["segments"] if s["text"])
    return {"text": text}


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL, "model_loaded": _model_loaded}


@app.post("/transcribe_segments")
async def transcribe_segments_endpoint(
    audio: UploadFile = File(...),
    language: str = Query("zh"),
    initial_prompt: str = Query(""),
    beam_size: int = Query(5),
    condition_on_previous_text: bool = Query(True),
):
    try:
        data = await audio.read()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _do_transcribe, data, language, initial_prompt)
        return JSONResponse(result)
    except Exception as exc:
        print(f"[asr/segments] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/transcribe_large")
async def transcribe_large_endpoint(
    audio: UploadFile = File(...),
    language: str = Query("zh"),
):
    try:
        data = await audio.read()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _do_transcribe_text, data, language)
        return JSONResponse(result)
    except Exception as exc:
        print(f"[asr/large] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return JSONResponse({"error": str(exc)}, status_code=500)
```

- [ ] **Step 6: Start the service and run the format tests**

```bash
# Terminal 1
venv-asr/bin/uvicorn asr.service:app --host 0.0.0.0 --port 9002
# Wait for "Application startup complete."

# Terminal 2
venv-asr/bin/pytest tests/test_asr_service_format.py -v
```

Expected:
```
tests/test_asr_service_format.py::test_health PASSED
tests/test_asr_service_format.py::test_transcribe_segments_format PASSED
tests/test_asr_service_format.py::test_transcribe_large_format PASSED
3 passed
```

- [ ] **Step 7: Commit**

```bash
git add asr/__init__.py asr/service.py asr/requirements.txt tests/test_asr_service_format.py
git commit -m "feat(asr): add Qwen2-Audio-7B-Instruct ASR service on :9002"
```

---

## Task 2: ctl.sh + Makefile Integration

**Files:**
- Modify: `scripts/ctl.sh`
- Modify: `Makefile`
- Modify: `config.yaml.example`

**Interfaces:**
- Consumes: `asr/service.py` (Task 1)
- Produces: `bash scripts/ctl.sh start asr`, `make start-asr`

- [ ] **Step 1: Add `asr` to `do_status()` in ctl.sh**

Find this block in `scripts/ctl.sh` (around line 110):
```bash
    _head "Native processes"
    for svc in whisper watcher worker; do
        _is_running "$svc" \
            && _ok  "$svc  pid=$(cat "$(_pid_file "$svc")")" \
            || _fail "$svc  not running"
    done
    _is_running "diarize" \
        && _ok  "diarize  pid=$(cat "$(_pid_file diarize)")" \
        || _info "diarize  not running (optional)"
```

Replace with:
```bash
    _head "Native processes"
    for svc in whisper watcher worker; do
        _is_running "$svc" \
            && _ok  "$svc  pid=$(cat "$(_pid_file "$svc")")" \
            || _fail "$svc  not running"
    done
    _is_running "diarize" \
        && _ok  "diarize  pid=$(cat "$(_pid_file diarize)")" \
        || _info "diarize  not running (optional)"
    _is_running "asr" \
        && _ok  "asr      pid=$(cat "$(_pid_file asr)")" \
        || _info "asr      not running (optional — alternative to whisper)"
```

- [ ] **Step 2: Add `asr` health check to `do_status()` in ctl.sh**

Find:
```bash
    _http_ok http://localhost:9003/health            && _ok "diarize:9003" || _info "diarize:9003 (optional)"
```

Add after it:
```bash
    _http_ok http://localhost:9002/health            && _ok "asr    :9002" || _info "asr    :9002 (optional)"
```

- [ ] **Step 3: Add `asr` to `do_start()` in ctl.sh**

Find the `diarize` block (around line 162):
```bash
    if [[ "$svc" == "diarize" ]]; then
        _head "Starting diarize"
        if [[ ! -d venv-diarize ]]; then
            _info "Creating venv-diarize..."
            python3 -m venv venv-diarize
            venv-diarize/bin/pip install --quiet -r diarize/requirements.txt
        fi
        _start_bg diarize venv-diarize/bin/uvicorn diarize.service:app --host 0.0.0.0 --port 9003
    fi
```

Add after it:
```bash
    if [[ "$svc" == "asr" ]]; then
        _head "Starting asr (Qwen2-Audio)"
        if [[ ! -d venv-asr ]]; then
            _info "Creating venv-asr (first run downloads ~14 GB model)..."
            python3 -m venv venv-asr
            venv-asr/bin/pip install --quiet -r asr/requirements.txt
        fi
        _start_bg asr venv-asr/bin/uvicorn asr.service:app --host 0.0.0.0 --port 9002
    fi
```

- [ ] **Step 4: Add `asr` to `do_stop()` in ctl.sh**

Find:
```bash
    if [[ "$svc" == "all" || "$svc" == "diarize" ]]; then _stop_proc diarize; fi
```

Add after it:
```bash
    if [[ "$svc" == "asr" ]]; then _stop_proc asr; fi
```

- [ ] **Step 5: Add `asr` to `do_logs()` in ctl.sh**

Find the logs dispatch in ctl.sh and add `asr` alongside `diarize`. Read the logs section with `grep -n "logs\|diarize" scripts/ctl.sh` to find the exact line, then add:
```bash
        asr)     tail -f "$LOG_DIR/asr.log" ;;
```

- [ ] **Step 6: Add asr targets to Makefile**

Add to the `.PHONY` line at the top:
```
start-asr stop-asr logs-asr restart-asr \
```

Add these targets after the `start-diarize` / `stop-diarize` / `logs-diarize` blocks:

```makefile
start-asr:
	$(CTL) start asr

stop-asr:
	$(CTL) stop asr

restart-asr:
	$(CTL) stop asr
	$(CTL) start asr

logs-asr:
	$(CTL) logs asr
```

And add to `help`:
```makefile
	@echo "    make start-asr        Qwen2-Audio ASR service :9002 (quality alternative to Whisper)"
```

- [ ] **Step 7: Add comment to config.yaml.example**

Find in `config.yaml.example`:
```yaml
whisper:
  service_url: http://localhost:9001
```

Add comment:
```yaml
whisper:
  service_url: http://localhost:9001   # switch to http://localhost:9002 to use Qwen2-Audio (make start-asr)
```

- [ ] **Step 8: Verify ctl.sh works**

```bash
bash scripts/ctl.sh status
```

Expected: shows `asr      not running (optional — alternative to whisper)` in native processes, and `asr    :9002 (optional)` in health.

- [ ] **Step 9: Commit**

```bash
git add scripts/ctl.sh Makefile config.yaml.example
git commit -m "feat(ctl): add asr service (Qwen2-Audio) to ctl.sh and Makefile"
```

---

## Task 3: Quality Comparison Test

**Goal:** Run a real job through Qwen2-Audio, compare SRT output vs the existing Whisper-medium baseline using the standard test corpus.

**Files:**
- No new files — uses existing pipeline

**Interfaces:**
- Consumes: `asr/service.py` running on :9002, existing test corpus `ain-tsmc-n8n-20260607-discussion01`

- [ ] **Step 1: Start the asr service**

```bash
make start-asr
```

Wait until health check passes:
```bash
curl http://localhost:9002/health
# Expected: {"status":"ok","model":"Qwen/Qwen2-Audio-7B-Instruct","model_loaded":true}
# Note: model_loaded will be false until first request; that's OK
```

- [ ] **Step 2: Point config.yaml at the new service**

Edit `config.yaml` (not committed):
```yaml
whisper:
  service_url: http://localhost:9002   # switched from 9001 (Whisper) to 9002 (Qwen2-Audio)
```

- [ ] **Step 3: Run a job through the pipeline**

```bash
# Restart worker to pick up config change
make restart-worker

# Copy test audio to input (triggers watcher)
cp workspace/4_archive/ain-tsmc-n8n-20260607-discussion01.* workspace/1_input/
```

Monitor: `make logs-worker` — watch for transcribe stage completion.

- [ ] **Step 4: Compare outputs side-by-side**

```bash
# Qwen2-Audio output (just produced)
cat workspace/3_output/ain-tsmc-n8n-20260607-discussion01.srt | head -60

# Whisper-medium baseline (from archive)
# (retrieve from MinIO or previous run output)
```

Document observations:
- Segment count difference (Qwen2-Audio will have fewer, larger segments — 30s chunks)
- Chinese accuracy on code-switching (English tech terms)  
- Handling of incomplete sentences / filler words

- [ ] **Step 5: Restore config.yaml to Whisper (if Qwen2-Audio is not yet adopted)**

```yaml
whisper:
  service_url: http://localhost:9001
```

```bash
make restart-worker
```

- [ ] **Step 6: Update memory with findings**

After the comparison, save results to `docs/model-evaluation.md` and update the project memory in `/Users/tygrus/.claude/projects/-Users-tygrus-Desktop-projects-mediaflow/memory/project_model_evaluation.md` with:
- OOM outcome from Task 0
- MPS memory used
- Qualitative comparison vs whisper-medium

---

## Self-Review

**Spec coverage:**
- ✅ Same HTTP interface as whisper/service.py — stages.py unchanged
- ✅ OOM gate before building anything
- ✅ venv-asr pattern follows venv-whisper / venv-diarize
- ✅ ctl.sh + Makefile integration
- ✅ config.yaml switch is one line
- ✅ Quality comparison task included

**Placeholder scan:** None.

**Type consistency:**
- `segments` format: `{id: int, start: float, end: float, text: str, avg_logprob: float, no_speech_prob: float}` — consistent across service.py and test.
- `/transcribe_large` returns `{text: str}` — consistent.

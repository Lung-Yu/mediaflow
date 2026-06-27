"""Qwen3-ASR + ForcedAligner MLX service — Apple Silicon native, no PyTorch.

Drop-in for whisper/service.py. Switch: whisper.service_url: http://localhost:9004 in config.yaml.

Endpoints:
  GET  /health               — liveness + model-loaded flag
  POST /transcribe_segments  — full audio → {segments: [{id, start, end, text, avg_logprob, no_speech_prob}]}
  POST /transcribe_large     — short clip → {text: "..."}

Flow per 300s chunk:
  ASR (Qwen3-ASR) → simplified text → ForcedAligner → word timestamps → sentence grouping → OpenCC
"""
import asyncio
import io
import os
import sys
from math import gcd
from pathlib import Path

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Query, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI()

ASR_MODEL = os.environ.get("ASR_MODEL", "mlx-community/Qwen3-ASR-0.6B-bf16")
ALIGNER_MODEL = os.environ.get("ALIGNER_MODEL", "mlx-community/Qwen3-ForcedAligner-0.6B-8bit")
ALIGNER_CHUNK_SEC = int(os.environ.get("ALIGNER_CHUNK_SEC", "300"))  # Aligner hard limit
SR = 16000
HARD_STOPS = frozenset("。！？")
MAX_SEG_CHARS = 20

_asr_model = None
_aligner = None
_cc = None


def _get_cc():
    global _cc
    if _cc is None:
        import opencc
        _cc = opencc.OpenCC("s2twp")
    return _cc


def _get_asr():
    global _asr_model
    if _asr_model is None:
        from mlx_qwen3_asr import Qwen3ASR
        _asr_model = Qwen3ASR.from_pretrained(ASR_MODEL)
    return _asr_model


def _get_aligner():
    global _aligner
    if _aligner is None:
        from mlx_qwen3_asr.forced_aligner import ForcedAligner
        _aligner = ForcedAligner(model_path=ALIGNER_MODEL)
    return _aligner


def _decode(wav_bytes: bytes) -> np.ndarray:
    audio, sr = sf.read(io.BytesIO(wav_bytes))
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    audio = audio.astype(np.float32)
    if sr != SR:
        from scipy.signal import resample_poly
        g = gcd(sr, SR)
        audio = resample_poly(audio, SR // g, sr // g).astype(np.float32)
    return audio


def _words_to_segments(words, offset_sec: float, seg_id_start: int) -> list[dict]:
    segments = []
    seg_id = seg_id_start
    buf_text = ""
    buf_start = None
    buf_end = None

    for w in words:
        if buf_start is None:
            buf_start = w.start_time + offset_sec
        buf_text += w.text
        buf_end = w.end_time + offset_sec

        if w.text and w.text[-1] in HARD_STOPS or len(buf_text) >= MAX_SEG_CHARS:
            text = _get_cc().convert(buf_text).strip()
            if text:
                segments.append({
                    "id": seg_id, "start": round(buf_start, 3), "end": round(buf_end, 3),
                    "text": text, "avg_logprob": 0.0, "no_speech_prob": 0.0,
                })
                seg_id += 1
            buf_text = ""
            buf_start = None

    if buf_text.strip():
        text = _get_cc().convert(buf_text).strip()
        if text:
            segments.append({
                "id": seg_id, "start": round(buf_start, 3), "end": round(buf_end, 3),
                "text": text, "avg_logprob": 0.0, "no_speech_prob": 0.0,
            })

    return segments


def _do_transcribe(wav_bytes: bytes, language: str) -> dict:
    asr = _get_asr()
    aligner = _get_aligner()
    audio = _decode(wav_bytes)

    chunk_samples = ALIGNER_CHUNK_SEC * SR
    total = len(audio)
    total_chunks = (total + chunk_samples - 1) // chunk_samples

    segments = []
    seg_id = 0

    for i in range(total_chunks):
        offset = i * chunk_samples
        chunk = audio[offset: offset + chunk_samples]
        offset_sec = offset / SR

        result = asr.transcribe(chunk, language=language)
        text = result.text.strip()
        if not text:
            print(f"[asr] chunk {i+1}/{total_chunks} empty", flush=True)
            continue

        words = aligner.align(chunk, text, language)
        if words:
            new_segs = _words_to_segments(words, offset_sec, seg_id)
        else:
            # ponytail: chunk-level fallback if aligner returns nothing
            new_segs = [{
                "id": seg_id, "start": round(offset_sec, 3),
                "end": round(min((offset + chunk_samples) / SR, total / SR), 3),
                "text": _get_cc().convert(text).strip(),
                "avg_logprob": 0.0, "no_speech_prob": 0.0,
            }]

        segments.extend(new_segs)
        seg_id += len(new_segs)
        print(f"[asr] chunk {i+1}/{total_chunks} → {len(new_segs)} segments", flush=True)

    return {"segments": segments}


def _do_transcribe_text(wav_bytes: bytes, language: str) -> dict:
    result = _do_transcribe(wav_bytes, language)
    return {"text": " ".join(s["text"] for s in result["segments"])}


@app.get("/health")
def health():
    return {"status": "ok", "asr_model": ASR_MODEL, "aligner_model": ALIGNER_MODEL,
            "asr_loaded": _asr_model is not None, "aligner_loaded": _aligner is not None}


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
        result = await loop.run_in_executor(None, _do_transcribe, data, language)
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

"""Qwen3-ASR-1.7B MLX ASR service — Apple Silicon native, no PyTorch.

Drop-in for whisper/service.py. Switch: whisper.service_url: http://localhost:9004 in config.yaml.

Endpoints:
  GET  /health               — liveness + model-loaded flag
  POST /transcribe_segments  — full audio → {segments: [{id, start, end, text, avg_logprob, no_speech_prob}]}
  POST /transcribe_large     — short clip → {text: "..."}

Usage:
  ASR_MODEL=mlx-community/Qwen3-ASR-1.7B-8bit uvicorn asr.service:app --host 0.0.0.0 --port 9004
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

MODEL = os.environ.get("ASR_MODEL", "mlx-community/Qwen3-ASR-0.6B-bf16")
CHUNK_SEC = int(os.environ.get("ASR_CHUNK_SEC", "30"))
SR = 16000

_model = None
_model_loaded = False


def _get_model():
    global _model, _model_loaded
    if _model is not None:
        return _model
    from qwen3_asr_mlx import Qwen3ASR
    _model = Qwen3ASR.from_pretrained(MODEL)
    _model_loaded = True
    return _model


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


def _do_transcribe(wav_bytes: bytes, language: str) -> dict:
    model = _get_model()
    audio = _decode(wav_bytes)

    chunk_samples = CHUNK_SEC * SR
    total = len(audio)
    total_chunks = (total + chunk_samples - 1) // chunk_samples

    segments, seg_id, offset = [], 0, 0
    while offset < total:
        chunk = audio[offset: offset + chunk_samples]
        start_sec = round(offset / SR, 3)
        end_sec = round(min((offset + chunk_samples) / SR, total / SR), 3)
        result = model.transcribe(chunk, language=language)
        text = result.text.strip()
        if text:
            segments.append({
                "id": seg_id,
                "start": start_sec,
                "end": end_sec,
                "text": text,
                # ponytail: no logprobs from Qwen3-ASR; 0.0 disables verify_segments flagging
                "avg_logprob": 0.0,
                "no_speech_prob": 0.0,
            })
            seg_id += 1
        offset += chunk_samples
        print(f"[asr] chunk {seg_id}/{total_chunks}", flush=True)

    return {"segments": segments}


def _do_transcribe_text(wav_bytes: bytes, language: str) -> dict:
    result = _do_transcribe(wav_bytes, language)
    text = " ".join(s["text"] for s in result["segments"])
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

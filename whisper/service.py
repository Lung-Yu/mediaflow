"""Whisper transcription HTTP service — mlx-whisper on Apple Silicon GPU.

Endpoints:
  GET  /health               — liveness + model-loaded flag
  POST /transcribe_segments  — full audio → {segments: [{id, start, end, text, avg_logprob, no_speech_prob}]}
  POST /transcribe_large     — short clip → {text: "..."}  (used by verify_segments stage)

Model: mlx-community/whisper-large-v3-mlx (fp16, ~3 GB)
Downloaded automatically on first request to ~/.cache/huggingface/.
Override via WHISPER_MODEL env var.

Usage:
  uvicorn whisper.service:app --host 0.0.0.0 --port 9001
"""
import asyncio
import math
import os
import sys
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, Query, UploadFile
from fastapi.responses import JSONResponse

app = FastAPI()

MODEL = os.environ.get("WHISPER_MODEL", "mlx-community/whisper-large-v3-mlx")
_model_loaded = False


def _do_transcribe(
    wav_bytes: bytes,
    language: str,
    initial_prompt: str,
    beam_size: int = 5,
    condition_on_previous_text: bool = True,
) -> dict:
    """Blocking — runs in thread pool executor."""
    import mlx_whisper
    global _model_loaded

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_bytes)
        path = Path(tmp.name)

    try:
        kwargs: dict = {"path_or_hf_repo": MODEL}
        # mlx-whisper raises NotImplementedError for beam_size > 1; skip it entirely
        # and rely on the library's greedy default
        kwargs["condition_on_previous_text"] = condition_on_previous_text
        if language:
            kwargs["language"] = language
        if initial_prompt:
            kwargs["initial_prompt"] = initial_prompt

        result = mlx_whisper.transcribe(str(path), **kwargs)
        _model_loaded = True

        def _f(v, default=0.0):
            return v if (v is not None and math.isfinite(v)) else default

        segments = [
            {
                "id": s.get("id", i),
                "start": _f(s["start"]),
                "end": _f(s["end"]),
                "text": s["text"],
                "avg_logprob": _f(s.get("avg_logprob")),
                "no_speech_prob": _f(s.get("no_speech_prob")),
            }
            for i, s in enumerate(result.get("segments", []))
        ]
        return {"segments": segments}
    finally:
        path.unlink(missing_ok=True)


def _do_transcribe_text(wav_bytes: bytes, language: str) -> dict:
    """Transcribe a short clip and return concatenated text (for verify_segments)."""
    result = _do_transcribe(wav_bytes, language, "")
    text = " ".join(s["text"].strip() for s in result["segments"] if s["text"].strip())
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
        result = await loop.run_in_executor(
            None, _do_transcribe, data, language, initial_prompt,
            beam_size, condition_on_previous_text,
        )
        return JSONResponse(result)
    except Exception as exc:
        print(f"[whisper/segments] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.post("/unload")
def unload():
    global _model_loaded
    try:
        import gc
        import mlx.core as mx
        import mlx_whisper
        for attr in ("_MODELS", "_model_cache"):
            cache = getattr(mlx_whisper, attr, None)
            if isinstance(cache, dict):
                cache.clear()
        mx.metal.clear_cache()
        gc.collect()
    except Exception as exc:
        print(f"[whisper/unload] {exc}", file=sys.stderr)
    _model_loaded = False
    return {"status": "unloaded"}


@app.post("/transcribe_large")
async def transcribe_large_endpoint(
    audio: UploadFile = File(...),
    language: str = Query("zh"),
):
    """Same model (large-v3 is already the largest); used by verify_segments for clip re-check."""
    try:
        data = await audio.read()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _do_transcribe_text, data, language)
        return JSONResponse(result)
    except Exception as exc:
        print(f"[whisper/large] {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return JSONResponse({"error": str(exc)}, status_code=500)

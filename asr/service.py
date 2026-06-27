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
from math import gcd
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


def _resample(audio: np.ndarray, src_sr: int, dst_sr: int) -> np.ndarray:
    from scipy.signal import resample_poly
    g = gcd(src_sr, dst_sr)
    return resample_poly(audio, dst_sr // g, src_sr // g).astype(np.float32)


def _transcribe_chunk(
    audio: np.ndarray, sr: int, language: str, prompt: str, model, processor
) -> str:
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


def _do_transcribe(wav_bytes: bytes, language: str, prompt: str) -> dict:
    """Blocking — runs in thread pool executor."""
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
                    # Qwen2-Audio exposes no logprobs; 0.0 disables verify_segments flagging
                    "avg_logprob": 0.0,
                    "no_speech_prob": 0.0,
                })
                seg_id += 1
            offset += chunk_samples

        return {"segments": segments}
    finally:
        wav_path.unlink(missing_ok=True)


def _do_transcribe_text(wav_bytes: bytes, language: str) -> dict:
    """Transcribe and return concatenated text (for verify_segments)."""
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
            None, _do_transcribe, data, language, initial_prompt
        )
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

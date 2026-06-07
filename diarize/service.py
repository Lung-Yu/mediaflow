"""Local speaker diarization HTTP service.

Uses speechbrain ECAPA-TDNN (Apache 2.0) for speaker embeddings
and sklearn AgglomerativeClustering. No HuggingFace token required.

Segmentation:
  - If 'segments' form field provided: use caller's timestamps (from Whisper)
  - Otherwise: treat the whole file as one segment

Models cached in ~/.cache/speechbrain/ on first request (~200 MB).

Usage:
  uvicorn diarize.service:app --port 9003
"""
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from sklearn.cluster import AgglomerativeClustering
from sklearn.preprocessing import normalize

app = FastAPI()

_encoder = None


def _best_device() -> str:
    try:
        import torch
        if torch.backends.mps.is_available():
            return "mps"
    except Exception:
        pass
    return "cpu"


def _get_encoder():
    global _encoder
    if _encoder is not None:
        return _encoder
    from speechbrain.inference.speaker import EncoderClassifier
    savedir = str(Path.home() / ".cache" / "speechbrain" / "spkrec-ecapa-voxceleb")
    _encoder = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb",
        savedir=savedir,
        run_opts={"device": _best_device()},
    )
    return _encoder


def _embed_clip(encoder, wav_path: Path) -> Optional[np.ndarray]:
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


def _cluster(embeddings: list, num_speakers: Optional[int], threshold: float) -> list:
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
    segments: Optional[str] = Form(None),
    num_speakers: Optional[int] = Query(None),
    cluster_threshold: float = Query(0.4),
):
    encoder = _get_encoder()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(await audio.read())
        wav_path = Path(tmp.name)

    try:
        if segments:
            segs = json.loads(segments)
        else:
            info = sf.info(str(wav_path))
            segs = [{"start": 0.0, "end": info.duration}]

        embeddings = []
        valid_idx = []
        clip_path = wav_path.parent / "diarize_clip.wav"

        for i, seg in enumerate(segs):
            try:
                subprocess.run(
                    ["ffmpeg", "-y", "-i", str(wav_path),
                     "-ss", str(seg["start"]), "-to", str(seg["end"]),
                     "-ar", "16000", "-ac", "1", str(clip_path)],
                    check=True, capture_output=True, timeout=30,
                )
            except subprocess.CalledProcessError:
                continue
            emb = _embed_clip(encoder, clip_path)
            if emb is not None:
                embeddings.append(emb)
                valid_idx.append(i)

        clip_path.unlink(missing_ok=True)

        if not embeddings:
            return JSONResponse({"segments": []})

        labels = _cluster(embeddings, num_speakers, cluster_threshold)

        result = [
            {
                "speaker": f"SPEAKER_{labels[label_idx]:02d}",
                "start": segs[seg_idx]["start"],
                "end": segs[seg_idx]["end"],
            }
            for label_idx, seg_idx in enumerate(valid_idx)
        ]
        return JSONResponse({"segments": result})

    finally:
        wav_path.unlink(missing_ok=True)

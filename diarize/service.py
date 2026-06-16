"""Local speaker diarization HTTP service.

Uses speechbrain ECAPA-TDNN (Apache 2.0) for speaker embeddings
and sklearn AgglomerativeClustering. No HuggingFace token required.

Segments the audio using caller-supplied timestamps (from Whisper) when provided.
Extracts per-segment embeddings in one FFmpeg pass per segment, batched.
Models cached in ~/.cache/speechbrain/ on first request (~200 MB).

Usage:
  uvicorn diarize.service:app --port 9003
"""
import asyncio
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf
import torch
from fastapi import FastAPI, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import normalize

app = FastAPI()

_encoder = None


def _best_device() -> str:
    # MPS causes 'device_type' AttributeError in speechbrain ECAPA-TDNN — use CPU
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


def _embed_segment(encoder, wav_path: Path, start: float, end: float) -> Optional[np.ndarray]:
    """Extract embedding for one segment using FFmpeg clip + ECAPA-TDNN."""
    duration = max(end - start, 0.1)
    clip = wav_path.parent / f"_clip_{id(wav_path)}_{int(start*1000)}.wav"
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(wav_path),
             "-ss", str(max(0, start - 0.05)),
             "-t", str(duration + 0.1),
             "-ar", "16000", "-ac", "1", str(clip)],
            check=True, capture_output=True, timeout=30,
        )
        sig, sr = sf.read(str(clip))
        if sig.ndim > 1:
            sig = sig.mean(axis=1)
        if len(sig) < sr * 0.1:
            return None
        tensor = torch.tensor(sig, dtype=torch.float32).unsqueeze(0)
        with torch.no_grad():
            emb = encoder.encode_batch(tensor)
        return emb.squeeze().cpu().numpy()
    except Exception:
        return None
    finally:
        clip.unlink(missing_ok=True)


def _embed_file(encoder, wav_path: Path) -> np.ndarray:
    """Extract a single embedding for an entire audio file (for enrollment)."""
    sig, sr = sf.read(str(wav_path))
    if sig.ndim > 1:
        sig = sig.mean(axis=1)
    tensor = torch.tensor(sig, dtype=torch.float32).unsqueeze(0)
    with torch.no_grad():
        emb = encoder.encode_batch(tensor)
    return emb.squeeze().cpu().numpy()


def _best_n_speakers(X: np.ndarray, max_n: int) -> int:
    """Pick optimal speaker count using silhouette score (higher = more distinct clusters).

    Tries n = 2..max_n, returns n with the highest silhouette score.
    Returns 1 if all scores are below 0.15 (embeddings too similar — likely 1 speaker).
    """
    best_n = 1
    best_score = -1.0
    for n in range(2, min(max_n + 1, len(X))):
        labels = AgglomerativeClustering(
            n_clusters=n, metric="cosine", linkage="average"
        ).fit_predict(X)
        if len(set(labels)) < 2:
            continue
        score = float(silhouette_score(X, labels, metric="cosine"))
        if score > best_score:
            best_score = score
            best_n = n
    return 1 if best_score < 0.15 else best_n


def _cluster(embeddings: list, num_speakers: Optional[int], max_auto: int) -> tuple:
    """Return (labels, {cluster_id: [embedding_arrays]})."""
    if len(embeddings) == 1:
        return [0], {0: [np.array(embeddings[0])]}
    X = normalize(np.array(embeddings))
    n = num_speakers if num_speakers else _best_n_speakers(X, max_auto)
    if n == 1:
        labels = [0] * len(X)
    else:
        try:
            from sklearn.cluster import SpectralClustering
            labels = SpectralClustering(
                n_clusters=n, affinity="cosine", assign_labels="kmeans", random_state=42
            ).fit_predict(X).tolist()
        except Exception:
            labels = AgglomerativeClustering(
                n_clusters=n, metric="cosine", linkage="average"
            ).fit_predict(X).tolist()
    cluster_embs: dict = {}
    for i, lbl in enumerate(labels):
        cluster_embs.setdefault(lbl, []).append(X[i])
    return labels, cluster_embs


def _cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    a_n = a / (np.linalg.norm(a) + 1e-8)
    b_n = b / (np.linalg.norm(b) + 1e-8)
    return float(np.dot(a_n, b_n))


def _match_clusters_to_library(
    cluster_embs: dict,
    library: list,
    threshold: float = 0.70,
) -> dict:
    """Map cluster IDs to display names; unmatched clusters get UNKNOWN_N.

    Each library entry: {"name": str, "embedding": [float, ...]}.
    Greedy: assigns the best-matching library speaker to each cluster in
    descending similarity order so no library name is used twice.
    """
    if not library:
        return {cid: f"SPEAKER_{cid:02d}" for cid in cluster_embs}

    lib_embs = [(e["name"], np.array(e["embedding"])) for e in library]

    scores = []
    for cid, embs in cluster_embs.items():
        centroid = np.mean(embs, axis=0)
        for name, emb in lib_embs:
            scores.append((_cosine_sim(centroid, emb), cid, name))
    scores.sort(reverse=True)

    assigned_clusters: set = set()
    assigned_names: set = set()
    matched: dict = {}
    for sim, cid, name in scores:
        if sim < threshold:
            break
        if cid in assigned_clusters or name in assigned_names:
            continue
        matched[cid] = name
        assigned_clusters.add(cid)
        assigned_names.add(name)

    unknown = 0
    for cid in cluster_embs:
        if cid not in matched:
            matched[cid] = f"UNKNOWN_{unknown}"
            unknown += 1
    return matched


def _process_embed(wav_bytes: bytes) -> dict:
    """Extract ECAPA-TDNN embedding for the entire audio clip. Blocking."""
    encoder = _get_encoder()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_bytes)
        wav_path = Path(tmp.name)
    try:
        emb = _embed_file(encoder, wav_path)
        return {"embedding": emb.tolist()}
    finally:
        wav_path.unlink(missing_ok=True)


def _process_diarize(
    wav_bytes: bytes,
    segs_json: Optional[str],
    num_speakers: Optional[int],
    max_auto_speakers: int,
    library_json: Optional[str],
    match_threshold: float,
) -> dict:
    """Blocking work — runs in a thread pool executor."""
    encoder = _get_encoder()

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(wav_bytes)
        wav_path = Path(tmp.name)

    try:
        if segs_json:
            segs = json.loads(segs_json)
        else:
            info = sf.info(str(wav_path))
            segs = [{"start": 0.0, "end": info.duration}]

        # Sliding windows with fixed time step for uniform temporal coverage.
        # Segment-based sampling over-samples speakers with many short utterances.
        audio_info = sf.info(str(wav_path))
        total_dur = audio_info.duration
        WINDOW_DUR = 2.0   # seconds per window
        WINDOW_STEP = 5.0  # seconds between window starts

        windows = []
        t = 0.0
        while t + WINDOW_DUR <= total_dur:
            windows.append({"start": t, "end": t + WINDOW_DUR})
            t += WINDOW_STEP
        if not windows:
            windows = [{"start": 0.0, "end": total_dur}]

        embeddings = []
        valid_windows = []
        for w in windows:
            emb = _embed_segment(encoder, wav_path, w["start"], w["end"])
            if emb is not None:
                embeddings.append(emb)
                valid_windows.append(w)

        if not embeddings:
            return {"segments": []}

        labels, cluster_embs = _cluster(embeddings, num_speakers, max_auto_speakers)

        library = json.loads(library_json) if library_json else []
        cluster_names = _match_clusters_to_library(cluster_embs, library, match_threshold)

        window_labels = [
            {"speaker": cluster_names[labels[i]],
             "start": valid_windows[i]["start"],
             "end": valid_windows[i]["end"]}
            for i in range(len(labels))
        ]

        def _overlap_speaker(seg_start: float, seg_end: float) -> str:
            """Assign speaker by overlap-weighted vote from windows."""
            votes: dict = {}
            for w in window_labels:
                overlap = max(0.0, min(seg_end, w["end"]) - max(seg_start, w["start"]))
                if overlap > 0:
                    votes[w["speaker"]] = votes.get(w["speaker"], 0.0) + overlap
            if not votes:
                seg_mid = (seg_start + seg_end) / 2
                nearest = min(window_labels, key=lambda w: abs((w["start"] + w["end"]) / 2 - seg_mid))
                return nearest["speaker"]
            return max(votes, key=votes.get)

        result = [
            {"speaker": _overlap_speaker(seg["start"], seg["end"]),
             "start": seg["start"],
             "end": seg["end"]}
            for seg in segs
        ]
        return {"segments": result}

    finally:
        wav_path.unlink(missing_ok=True)


@app.get("/health")
def health():
    return {"status": "ok", "model_loaded": _encoder is not None}


@app.post("/embed")
async def embed_endpoint(audio: UploadFile = File(...)):
    """Extract ECAPA-TDNN embedding from an audio file for speaker enrollment."""
    try:
        wav_bytes = await audio.read()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, _process_embed, wav_bytes)
        return JSONResponse(result)
    except Exception as exc:
        print(f"[diarize/embed] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)


@app.post("/diarize")
async def diarize_endpoint(
    audio: UploadFile = File(...),
    segments: Optional[str] = Form(None),
    library: Optional[str] = Form(None),
    num_speakers: Optional[int] = Query(None),
    max_speakers: int = Query(6),
    match_threshold: float = Query(0.70),
):
    try:
        wav_bytes = await audio.read()
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            _process_diarize,
            wav_bytes,
            segments,
            num_speakers,
            max_speakers,
            library,
            match_threshold,
        )
        return JSONResponse(result)
    except Exception as exc:
        print(f"[diarize] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=500)

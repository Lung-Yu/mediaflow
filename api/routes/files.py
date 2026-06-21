"""SRT file access — list, view, search transcripts, speaker labels."""
import json
import os
import re
from pathlib import Path
from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import FileResponse, PlainTextResponse
from api.utils import srt as srtlib
from api import db

router = APIRouter(prefix="/files")

WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "./workspace"))
OUTPUT_DIR = WORKSPACE / "3_output"
PROCESSING_DIR = WORKSPACE / "2_processing"
ARCHIVE_DIR = WORKSPACE / "4_archive"

_AUDIO_MIME = {
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".flac": "audio/flac",
}

def _find_audio(stem: str) -> tuple[Path, str] | None:
    """Return (path, mime) for the best available audio source, or None."""
    wav = PROCESSING_DIR / f"{stem}_clean.wav"
    if wav.exists():
        return wav, "audio/wav"
    for ext, mime in _AUDIO_MIME.items():
        p = ARCHIVE_DIR / f"{stem}{ext}"
        if p.exists():
            return p, mime
    return None


def _srt_path(stem: str) -> Path:
    return OUTPUT_DIR / f"{stem}.srt"


# ── List ─────────────────────────────────────────────────────
@router.get("/")
def list_srts():
    if not OUTPUT_DIR.exists():
        return []
    files = sorted(OUTPUT_DIR.glob("*.srt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [
        {
            "stem": p.stem,
            "size_kb": round(p.stat().st_size / 1024, 1),
            "mtime": p.stat().st_mtime,
        }
        for p in files
    ]


# ── Delete ───────────────────────────────────────────────────
@router.delete("/{stem}")
async def delete_file(stem: str):
    if not re.match(r'^[A-Za-z0-9_\-一-鿿　-〿]+$', stem):
        raise HTTPException(status_code=400, detail="Invalid stem")
    deleted = []
    for suffix in [".srt", "_summary.md", "_summary.json", "_diarization.json",
                   "_chapters.json", "_speaker_names.json"]:
        p = OUTPUT_DIR / f"{stem}{suffix}"
        if p.exists():
            p.unlink()
            deleted.append(p.name)
    await db.delete_task(stem)
    return {"deleted": deleted}


# ── Raw SRT text ──────────────────────────────────────────────
@router.get("/{stem}/srt", response_class=PlainTextResponse)
def get_srt(stem: str):
    path = _srt_path(stem)
    if not path.exists():
        raise HTTPException(status_code=404, detail="SRT not found")
    return path.read_text(encoding="utf-8", errors="replace")


@router.put("/{stem}/srt")
def save_srt(stem: str, body: dict = Body(...)):
    if not re.match(r'^[A-Za-z0-9_\-一-鿿　-〿]+$', stem):
        raise HTTPException(status_code=400, detail="Invalid stem")
    path = _srt_path(stem)
    if not path.exists():
        raise HTTPException(status_code=404, detail="SRT not found")
    content = body.get("content", "")
    if not isinstance(content, str):
        raise HTTPException(status_code=422, detail="content must be a string")
    path.write_text(content, encoding="utf-8")
    return {"saved": True, "bytes": len(content.encode())}


# ── Summary text ──────────────────────────────────────────────
@router.get("/{stem}/summary", response_class=PlainTextResponse)
def get_summary(stem: str):
    path = OUTPUT_DIR / f"{stem}_summary.md"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Summary not found")
    return path.read_text(encoding="utf-8", errors="replace")


# ── Audio file ────────────────────────────────────────────────
@router.get("/{stem}/audio")
def get_audio(stem: str):
    result = _find_audio(stem)
    if result is None:
        raise HTTPException(status_code=404, detail="Audio not found")
    path, mime = result
    return FileResponse(path, media_type=mime)


# ── Parsed segments (JSON) ────────────────────────────────────
@router.get("/{stem}/segments")
def get_segments(stem: str, q: str = Query(default="")):
    path = _srt_path(stem)
    if not path.exists():
        raise HTTPException(status_code=404, detail="SRT not found")
    segments = srtlib.parse(path)
    if q:
        segments = srtlib.search(segments, q)
    return [
        {
            "index": s.index,
            "start": s.start,
            "end": s.end,
            "start_seconds": srtlib.to_seconds(s.start),
            "text": srtlib.highlight(s.text, q) if q else s.text,
        }
        for s in segments
    ]


# ── Speaker names ─────────────────────────────────────────────
@router.get("/{stem}/speaker-names")
def get_speaker_names(stem: str):
    """Return detected speakers + any saved display names for this file."""
    names_path = OUTPUT_DIR / f"{stem}_speaker_names.json"
    diar_path = OUTPUT_DIR / f"{stem}_diarization.json"

    names: dict = {}
    if names_path.exists():
        try:
            names = json.loads(names_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    speakers: list = []
    counts: dict = {}
    if diar_path.exists():
        try:
            diar = json.loads(diar_path.read_text(encoding="utf-8"))
            seen: set = set()
            for s in diar:
                sp = s.get("speaker", "")
                if not sp:
                    continue
                if sp not in seen:
                    seen.add(sp)
                    speakers.append(sp)
                counts[sp] = counts.get(sp, 0) + 1
            speakers.sort()
        except Exception:
            pass

    has_audio = _find_audio(stem) is not None
    return {"speakers": speakers, "counts": counts, "names": names, "has_audio": has_audio}


@router.post("/{stem}/speaker-names")
def set_speaker_names(stem: str, body: dict = Body(...)):
    """Save display name mapping for this file's speakers."""
    names_path = OUTPUT_DIR / f"{stem}_speaker_names.json"
    clean = {k: v for k, v in body.items() if isinstance(v, str) and v.strip()}
    names_path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"saved": len(clean)}

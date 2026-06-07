"""SRT file access — list, view, and search transcripts."""
import os
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from api import srt as srtlib

router = APIRouter(prefix="/files")

WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "./workspace"))
OUTPUT_DIR = WORKSPACE / "3_output"


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


# ── Raw SRT text ──────────────────────────────────────────────
@router.get("/{stem}/srt", response_class=PlainTextResponse)
def get_srt(stem: str):
    path = _srt_path(stem)
    if not path.exists():
        raise HTTPException(status_code=404, detail="SRT not found")
    return path.read_text(encoding="utf-8", errors="replace")


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
            "text": srtlib.highlight(s.text, q) if q else s.text,
        }
        for s in segments
    ]

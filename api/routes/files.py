"""SRT file access — works both via volume mount (same-machine) and streaming."""
from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pathlib import Path
import os

router = APIRouter(prefix="/files")

WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "./workspace"))


@router.get("/{stem}/srt", response_class=PlainTextResponse)
def get_srt(stem: str):
    path = WORKSPACE / "3_output" / f"{stem}.srt"
    if not path.exists():
        raise HTTPException(status_code=404, detail="SRT not found")
    return path.read_text()

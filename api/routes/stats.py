"""Aggregate statistics over all pipeline tasks and output files."""
import json
import os
from pathlib import Path

from fastapi import APIRouter

from api import db

router = APIRouter(prefix="/stats")

WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "./workspace"))
OUTPUT_DIR = WORKSPACE / "3_output"


def _speaker_totals(output_dir: Path) -> list[dict]:
    totals: dict[str, float] = {}
    for path in output_dir.glob("*_diarization.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            segs = data if isinstance(data, list) else []
        except Exception:
            continue
        for seg in segs:
            if not isinstance(seg, dict):
                continue
            sp = seg.get("speaker", "")
            if sp:
                totals[sp] = totals.get(sp, 0.0) + (seg.get("end", 0) - seg.get("start", 0))
    total_all = sum(totals.values()) or 1.0
    return [
        {"label": sp, "seconds": round(secs, 1), "pct": round(secs / total_all, 3)}
        for sp, secs in sorted(totals.items(), key=lambda x: -x[1])
    ]


def _keyword_counts(output_dir: Path) -> list[dict]:
    counts: dict[str, int] = {}
    for path in output_dir.glob("*_summary.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        for seg in (data.get("topic_segments") or []):
            if not isinstance(seg, dict):
                continue
            topic = (seg.get("topic") or "").strip()
            if topic:
                counts[topic] = counts.get(topic, 0) + 1
    return [
        {"topic": t, "count": c}
        for t, c in sorted(counts.items(), key=lambda x: -x[1])[:10]
    ]


@router.get("/overview")
async def get_overview():
    agg = await db.get_task_aggregates()
    total = agg["total_tasks"]
    success_rate = (agg["completed"] / total) if total > 0 else 0.0
    return {
        "total_tasks": total,
        "total_duration_sec": agg["total_duration_sec"],
        "success_rate": round(success_rate, 3),
        "speakers": _speaker_totals(OUTPUT_DIR),
    }


@router.get("/keywords")
def get_keywords():
    return _keyword_counts(OUTPUT_DIR)

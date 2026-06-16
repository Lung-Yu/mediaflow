"""Per-recording speaker enrollment + diarize + SRT patch for yunli interview recordings.

For each recording:
  1. Preprocess m4a → clean WAV (skipped if WAV already exists)
  2. Identify Lung's speech segments by content keywords
  3. Concatenate those clips → embed → per-recording Lung library
  4. Call /diarize with library (num_speakers=2)
  5. Patch the existing SRT with 【Lung】/【候選人】 labels

Usage:
  source venv-diarize/bin/activate
  python scripts/diarize_interviews.py [--stem <stem>] [--dry-run]
"""
import argparse
import json
import logging
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

import httpx
import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

WORKSPACE   = Path(__file__).parent.parent / "workspace"
ARCHIVE     = WORKSPACE / "4_archive"
PROCESSING  = WORKSPACE / "2_processing"
OUTPUT      = WORKSPACE / "3_output"
DIARIZE_URL = "http://localhost:9003"

# Keywords that only appear in Lung's speech
LUNG_KW = [
    "我先講", "我先介紹", "我會說明", "我先請",
    "我們公司", "我們團隊", "我們在找", "我們希望",
    "公司規劃", "目前規劃", "服務項目", "服務方向",
    "這個職位", "這個職務",
    "你目前預計", "你有沒有發現", "當你做一個專案",
    "你覺得說", "你有沒有",
]

ALL_STEMS = [
    "yunli-t2-20260514-chen-shuxian",
    "yunli-t2-20260514-yang-qixian",
    "yunli-t2-20260605-ma-yixuan",
    "yunli-t2-20260605-shen-ruting",
    "yunli-t2-20260609-luo-kuiyan",
    "yunli-t2-20260609-ye-yuling",
    "yunli-t2-20260610-chen-yuru",
]

SPEAKER_NAMES = {
    "Lung":    "Lung",
    "UNKNOWN_0": "候選人",
}
SPEAKER_FMT = "【{speaker}】"


# ── Helpers ──────────────────────────────────────────────────────────────────

def _srt_time_to_s(t: str) -> float:
    h, m, rest = t.split(":")
    s, ms = rest.replace(",", ".").split(".")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _parse_srt(text: str) -> list[dict]:
    blocks = []
    for chunk in re.split(r"\n\s*\n", text.strip()):
        lines = chunk.strip().splitlines()
        if len(lines) < 3:
            continue
        time_line = lines[1]
        m = re.match(r"([\d:,]+)\s+-->\s+([\d:,]+)", time_line)
        if not m:
            continue
        blocks.append({
            "time": time_line,
            "start": _srt_time_to_s(m.group(1)),
            "end":   _srt_time_to_s(m.group(2)),
            "text":  "\n".join(lines[2:]),
        })
    return blocks


def _overlap_speaker(start: float, end: float, diar: list) -> Optional[str]:
    best, best_ovlp = None, 0.0
    for seg in diar:
        ovlp = min(end, seg["end"]) - max(start, seg["start"])
        if ovlp > best_ovlp:
            best_ovlp = ovlp
            best      = seg["speaker"]
    return best


# ── Stage 1: Preprocess ───────────────────────────────────────────────────────

def preprocess(stem: str) -> Path:
    wav = PROCESSING / f"{stem}_clean.wav"
    if wav.exists():
        log.info("preprocess: WAV exists, skipping (%s)", wav.name)
        return wav

    m4a = ARCHIVE / f"{stem}.m4a"
    if not m4a.exists():
        raise FileNotFoundError(f"Archive m4a not found: {m4a}")

    PROCESSING.mkdir(parents=True, exist_ok=True)
    af = (
        "aformat=channel_layouts=mono:sample_rates=16000,"
        "highpass=f=80,afftdn=nf=-25,anlmdn=s=7:p=0.002:r=0.002:m=15,"
        "speechnorm=e=12.5:r=0.00001:l=1,"
        "equalizer=f=1500:width_type=o:width=2:g=3,"
        "loudnorm=I=-16:TP=-1.5:LRA=11,"
        "dynaudnorm=f=200:g=11:p=0.95:m=5.0,"
        "silenceremove=start_periods=1:start_silence=0.5:start_threshold=-50dB:detection=peak"
    )
    log.info("preprocess: %s → %s", m4a.name, wav.name)
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(m4a), "-af", af,
         "-ar", "16000", "-ac", "1", "-vn", str(wav)],
        check=True, capture_output=True, timeout=600,
    )
    return wav


# ── Stage 2: Per-recording Lung enrollment ────────────────────────────────────

def _lung_segments_from_json(stem: str) -> list[dict]:
    """Return segments confirmed as Lung by keyword match from segments JSON."""
    seg_path = OUTPUT / f"{stem}_segments.json"
    if not seg_path.exists():
        return []
    segs = json.loads(seg_path.read_text(encoding="utf-8"))
    matched = [s for s in segs if any(kw in s.get("text", "") for kw in LUNG_KW)]
    # Require at least 1.0s duration per segment for reliable embedding
    return [s for s in matched if (s["end"] - s["start"]) >= 1.0]


def _concat_clips(wav: Path, segments: list[dict]) -> Optional[Path]:
    """Extract and concat specific segments into one WAV clip."""
    clips = []
    try:
        for s in segments:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            clip = Path(tmp.name)
            tmp.close()
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(wav),
                 "-ss", str(s["start"]), "-t", str(s["end"] - s["start"]),
                 "-ar", "16000", "-ac", "1", str(clip)],
                check=True, capture_output=True, timeout=30,
            )
            clips.append(clip)

        if not clips:
            return None

        # Write ffmpeg concat list
        lst = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
        for c in clips:
            lst.write(f"file '{c}'\n")
        lst.close()
        lst_path = Path(lst.name)

        out = Path(tempfile.mktemp(suffix=".wav"))
        subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", str(lst_path), "-ar", "16000", "-ac", "1", str(out)],
            check=True, capture_output=True, timeout=60,
        )
        lst_path.unlink(missing_ok=True)
        return out
    finally:
        for c in clips:
            Path(c).unlink(missing_ok=True)


def enroll_lung(stem: str, wav: Path) -> list[dict]:
    # Use content-filtered segments (keyword-confirmed Lung speech only)
    lung_segs = _lung_segments_from_json(stem)
    if not lung_segs:
        log.warning("enroll: no keyword-matched Lung segments found for %s", stem)
        return []

    total_dur = sum(s["end"] - s["start"] for s in lung_segs)
    log.info("enroll: %d Lung segments, %.1fs total audio", len(lung_segs), total_dur)

    concat = _concat_clips(wav, lung_segs)
    if not concat:
        return []

    client = httpx.Client(timeout=120.0)
    try:
        with open(concat, "rb") as f:
            resp = client.post(f"{DIARIZE_URL}/embed", files={"audio": f})
        resp.raise_for_status()
        emb = resp.json().get("embedding")
        if not emb:
            return []
        avg = np.array(emb)
        avg = avg / np.linalg.norm(avg)
        log.info("enroll: Lung embedding ready (%dD)", len(avg))
        return [{"name": "Lung", "embedding": avg.tolist()}]
    finally:
        concat.unlink(missing_ok=True)


# ── Stage 3: Diarize ─────────────────────────────────────────────────────────

def diarize(stem: str, wav: Path, library: list) -> list[dict]:
    seg_path = OUTPUT / f"{stem}_segments.json"
    whisper_segs = None
    if seg_path.exists():
        raw = json.loads(seg_path.read_text(encoding="utf-8"))
        whisper_segs = [{"start": s["start"], "end": s["end"]} for s in raw]

    data: dict = {}
    if whisper_segs:
        data["segments"] = json.dumps(whisper_segs)
    if library:
        data["library"] = json.dumps(library)

    log.info("diarize: sending request for %s (%d windows will be sampled)…", stem,
             int(wav.stat().st_size / 16000 / 2 / 5))  # rough estimate

    with open(wav, "rb") as f:
        resp = httpx.post(
            f"{DIARIZE_URL}/diarize",
            files={"audio": (wav.name, f)},
            params={"num_speakers": 2, "match_threshold": 0.55},
            data=data,
            timeout=600.0,
        )
    resp.raise_for_status()
    segs = resp.json().get("segments", [])

    # Save diarization JSON
    diar_path = OUTPUT / f"{stem}_diarization.json"
    diar_path.write_text(json.dumps(segs, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter
    dist = Counter(s["speaker"] for s in segs)
    log.info("diarize: %s → %s", stem, dict(dist))
    return segs


# ── Stage 4: SRT patch ───────────────────────────────────────────────────────

def patch_srt(stem: str, diar: list) -> Path:
    srt_path = OUTPUT / f"{stem}.srt"
    if not srt_path.exists():
        raise FileNotFoundError(f"SRT not found: {srt_path}")

    blocks = _parse_srt(srt_path.read_text(encoding="utf-8"))
    lines = []
    for i, block in enumerate(blocks):
        raw = _overlap_speaker(block["start"], block["end"], diar)
        if raw:
            display = SPEAKER_NAMES.get(raw, raw)
            text = SPEAKER_FMT.format(speaker=display) + block["text"]
        else:
            text = block["text"]
        lines.append(f"{i + 1}\n{block['time']}\n{text}\n")

    srt_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("SRT patched: %s", srt_path.name)
    return srt_path


# ── Main ──────────────────────────────────────────────────────────────────────



def run_one(stem: str, dry_run: bool = False) -> None:
    log.info("=== %s ===", stem)
    if dry_run:
        log.info("DRY RUN — no writes")
        return

    wav  = preprocess(stem)
    lib  = enroll_lung(stem, wav)
    diar = diarize(stem, wav, lib)
    patch_srt(stem, diar)
    log.info("done: %s", stem)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stem", help="Process only this stem (default: all)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    stems = [args.stem] if args.stem else ALL_STEMS
    for stem in stems:
        try:
            run_one(stem, dry_run=args.dry_run)
        except Exception as exc:
            log.error("FAILED %s: %s", stem, exc)


if __name__ == "__main__":
    main()

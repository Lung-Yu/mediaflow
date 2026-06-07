"""Pipeline stage runners — blocking, designed to run in a thread pool.

Stages in order:
  1. preprocess  — FFmpeg speech-enhancement + 16kHz WAV
  2. transcribe  — Whisper HTTP service → SRT file
  3. summarize   — Ollama (qwen2.5:7b) → _summary.md + _summary.json

Each function returns the primary output path and raises on failure.
Verification and LLM correction are future additions (P4 roadmap).
"""
import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
import ollama as _ollama

log = logging.getLogger(__name__)


# ── Stage 1: Preprocessing ──────────────────────────────────────────────────

def preprocess(input_path: Path, workspace: Path, cfg: dict) -> Path:
    """Speech-enhancement pipeline + 16 kHz mono WAV.

    Filter chain (same as production automate/pipeline):
      aformat → highpass → afftdn → anlmdn → speechnorm →
      equalizer → loudnorm → dynaudnorm → silenceremove
    """
    proc_dir = workspace / "2_processing"
    proc_dir.mkdir(parents=True, exist_ok=True)
    out = proc_dir / f"{input_path.stem}_clean.wav"

    af = (
        "aformat=channel_layouts=mono:sample_rates=16000,"
        "highpass=f=80,"
        "afftdn=nf=-25,"
        "anlmdn=s=7:p=0.002:r=0.002:m=15,"
        "speechnorm=e=12.5:r=0.00001:l=1,"
        "equalizer=f=1500:width_type=o:width=2:g=3,"
        "loudnorm=I=-16:TP=-1.5:LRA=11,"
        "dynaudnorm=f=200:g=11:p=0.95:m=5.0,"
        "silenceremove=start_periods=1:start_silence=0.5:start_threshold=-50dB:detection=peak"
    )
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_path), "-af", af,
             "-ar", "16000", "-ac", "1", "-vn", str(out)],
            check=True, capture_output=True, timeout=600,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"FFmpeg failed for {input_path.name}: {exc.stderr[-400:].decode(errors='replace')}"
        ) from exc
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found — install via: brew install ffmpeg")

    log.info("preprocess done: %s → %s", input_path.name, out.name)
    return out


# ── Stage 2: Transcription ──────────────────────────────────────────────────

def _seconds_to_srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _segments_to_srt(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, start=1):
        text = seg["text"].strip()
        if not text:
            continue
        start = _seconds_to_srt_time(seg["start"])
        end = _seconds_to_srt_time(seg["end"])
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def transcribe(audio_path: Path, stem: str, output_dir: Path, cfg: dict) -> Path:
    """POST to Whisper HTTP service (/transcribe_segments) and save SRT."""
    output_dir.mkdir(parents=True, exist_ok=True)
    srt_path = output_dir / f"{stem}.srt"

    service_url = cfg["whisper"]["service_url"].rstrip("/")
    language = cfg["whisper"].get("language", "zh")

    try:
        with open(audio_path, "rb") as f:
            resp = httpx.post(
                f"{service_url}/transcribe_segments",
                files={"audio": (audio_path.name, f)},
                params={"language": language},
                timeout=1800.0,
            )
        resp.raise_for_status()
    except httpx.ConnectError:
        raise RuntimeError(
            f"Cannot reach Whisper service at {service_url}. "
            "Start it before running the pipeline."
        )
    except httpx.HTTPStatusError as exc:
        raise RuntimeError(
            f"Whisper service error {exc.response.status_code}: {exc.response.text[:300]}"
        ) from exc

    segments = resp.json().get("segments", [])
    srt_content = _segments_to_srt(segments)
    srt_path.write_text(srt_content, encoding="utf-8")

    log.info("transcribe done: %s → %s (%d segments)", stem, srt_path.name, len(segments))
    return srt_path


# ── Stage 3: Summarization ──────────────────────────────────────────────────

def _parse_srt_blocks(srt_content: str) -> list[dict]:
    blocks = []
    for raw in re.split(r"\n\s*\n", srt_content.strip()):
        lines = raw.strip().splitlines()
        if len(lines) < 3:
            continue
        blocks.append({"time": lines[1].strip(), "text": "\n".join(lines[2:]).strip()})
    return blocks


def _start_hms(time_line: str) -> str:
    return time_line.split("-->")[0].strip().split(",")[0]


def _end_seconds(time_line: str) -> float:
    end = time_line.split("-->")[1].strip()
    h, m, rest = end.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _fmt_duration(s: float) -> str:
    s = int(s)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _ollama_chat(model: str, prompt: str) -> str:
    try:
        resp = _ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
        return resp["message"]["content"].strip()
    except Exception as exc:
        log.warning("Ollama unavailable: %s", exc)
        return ""


def summarize(stem: str, srt_path: Path, output_dir: Path, cfg: dict) -> Path:
    """Generate structured summary from SRT via Ollama.

    Outputs:
      {stem}_summary.md   — human-readable markdown
      {stem}_summary.json — structured data for downstream tools
    Returns the .md path.
    Never raises — falls back gracefully if Ollama is down.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    md_path = output_dir / f"{stem}_summary.md"
    json_path = output_dir / f"{stem}_summary.json"

    model = cfg["ollama"].get("model", "qwen2.5:7b")
    srt_content = srt_path.read_text(encoding="utf-8", errors="replace")
    blocks = _parse_srt_blocks(srt_content)

    if not blocks:
        log.warning("summarize: no SRT blocks in %s", srt_path.name)
        md_path.write_text("# 摘要\n\n（無內容）\n", encoding="utf-8")
        json_path.write_text(
            json.dumps({"summary": "", "key_moments": [], "topic_segments": []},
                       ensure_ascii=False), encoding="utf-8"
        )
        return md_path

    duration_s = _end_seconds(blocks[-1]["time"])
    full_text = "\n".join(b["text"] for b in blocks)
    ts_lines = [f"[{_start_hms(b['time'])}] {b['text']}" for b in blocks]

    # A. Overall summary
    overview = _ollama_chat(model, (
        "以下是一段課程或會議錄音的逐字稿，請生成3到5句話的整體摘要，"
        "使用繁體中文，不要加標題，直接輸出摘要內容：\n\n" + full_text[:6000]
    ))

    # B. Key moments (one chunked call for large files)
    chunk_size = 150
    all_moments: list[dict] = []
    for i in range(0, len(ts_lines), chunk_size):
        chunk = "\n".join(ts_lines[i:i + chunk_size])
        raw = _ollama_chat(model, (
            "以下是一段字幕片段（格式：[HH:MM:SS] 內容）。\n"
            "請找出最多2個重要時刻，每個輸出一行，格式：[HH:MM:SS] 描述（繁體中文）\n"
            "只輸出符合格式的行，不要其他說明：\n\n" + chunk
        ))
        for line in raw.splitlines():
            m = re.match(r"\[?(\d{2}:\d{2}:\d{2})\]?\s*(.+)", line.strip())
            if m:
                h, mi, s = m.group(1).split(":")
                all_moments.append({
                    "time": m.group(1),
                    "seconds": int(h) * 3600 + int(mi) * 60 + int(s),
                    "note": m.group(2).strip(),
                })

    # Deduplicate moments by 60s minimum gap
    all_moments.sort(key=lambda x: x["seconds"])
    moments: list[dict] = []
    for mo in all_moments:
        if not moments or mo["seconds"] - moments[-1]["seconds"] >= 60:
            moments.append(mo)
    moments = moments[:10]

    # C. Topic segments (sampled view)
    sampled = ts_lines[::12][:200]
    topics_raw = _ollama_chat(model, (
        "以下是一段錄音的逐字稿摘錄。請列出2到6個主要主題，"
        "每個主題用4到10個字概括，每行只寫一個主題名稱，不要編號：\n\n"
        + "\n".join(sampled)
    ))
    topic_names = [
        ln.strip(" \t•·-–：:.,。，")
        for ln in topics_raw.splitlines()
        if 3 <= len(ln.strip()) <= 20
    ][:6]

    # Find anchor timestamps for each topic via keyword search
    topic_segments: list[dict] = []
    for name in topic_names:
        tokens = [name[i:i+2] for i in range(len(name) - 1) if len(name[i:i+2]) == 2]
        tokens += re.findall(r"[A-Za-z0-9]+", name)
        anchor = None
        for line in ts_lines:
            text_part = line.split("] ", 1)[-1]
            if any(tok in text_part for tok in tokens if len(tok) >= 2):
                m2 = re.match(r"\[(\d{2}:\d{2}:\d{2})\]", line)
                if m2:
                    anchor = m2.group(1)
                    break
        if anchor:
            topic_segments.append({"topic": name, "start": anchor})

    # Derive end times from next segment's start
    for i, seg in enumerate(topic_segments):
        seg["end"] = topic_segments[i + 1]["start"] if i + 1 < len(topic_segments) else _fmt_duration(duration_s)

    # Build markdown
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    md_lines = [
        f"# 摘要 — {srt_path.name}",
        f"> 音檔長度：{_fmt_duration(duration_s)} ｜ 生成：{now} ｜ 模型：{model}",
        "",
        "## 整體摘要",
        overview or "（Ollama 未回應）",
        "",
    ]
    if topic_segments:
        md_lines += ["## 主題段落", "| 時間區間 | 主題 |", "|----------|------|"]
        for seg in topic_segments:
            md_lines.append(f"| {seg['start']} – {seg['end']} | {seg['topic']} |")
        md_lines.append("")
    if moments:
        md_lines.append("## 關鍵時刻")
        for mo in moments:
            md_lines.append(f"- `[{mo['time']}]` {mo['note']}")
        md_lines.append("")

    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    json_data = {
        "source_file": srt_path.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": round(duration_s),
        "duration_fmt": _fmt_duration(duration_s),
        "model": model,
        "summary": overview,
        "topic_segments": topic_segments,
        "key_moments": moments,
        "segment_count": len(blocks),
    }
    json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("summarize done: %s → %s + %s", stem, md_path.name, json_path.name)
    return md_path

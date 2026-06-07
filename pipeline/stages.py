"""Pipeline stage runners — blocking, designed to run in a thread pool.

Stages in order:
  1. preprocess   — FFmpeg speech-enhancement + 16kHz WAV
  2. transcribe   — Whisper HTTP service → SRT file
  2b. correct_srt — optional Ollama pass to fix STT errors (llm_correction: true)
  3. summarize    — Ollama → _summary.md + _summary.json

Each function returns the primary output path and raises on failure.
"""
import json
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import httpx
import ollama as _ollama

from pipeline.prompts import PROMPTS

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
    # Filter empty segments before saving — keeps positional alignment with the SRT.
    nonempty = [s for s in segments if s.get("text", "").strip()]
    srt_content = _segments_to_srt(segments)
    srt_path.write_text(srt_content, encoding="utf-8")

    seg_path = output_dir / f"{stem}_segments.json"
    seg_path.write_text(json.dumps(nonempty, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("transcribe done: %s → %s (%d segments)", stem, srt_path.name, len(nonempty))
    return srt_path


# ── Stage 2b: Segment Verification ─────────────────────────────────────────

def verify_segments(stem: str, srt_path: Path, audio_path: Path, cfg: dict) -> Path:
    """Re-transcribe low-confidence segments using whisper-large-v3.

    Reads {stem}_segments.json for avg_logprob / no_speech_prob per segment.
    Clips suspicious segments with FFmpeg, POSTs to /transcribe_large,
    and replaces text in the SRT when the result differs.
    Never raises — falls back to original SRT on any failure.
    """
    seg_path = srt_path.parent / f"{stem}_segments.json"
    if not seg_path.exists():
        log.warning("verify_segments: no segments JSON for %s — run transcribe first", stem)
        return srt_path
    if not audio_path.exists():
        log.warning("verify_segments: clean WAV not found for %s", stem)
        return srt_path

    segments = json.loads(seg_path.read_text(encoding="utf-8"))
    v_cfg = cfg.get("verification", {})
    logprob_thresh = float(v_cfg.get("logprob_threshold", -0.8))
    no_speech_thresh = float(v_cfg.get("no_speech_threshold", 0.7))
    service_url = cfg["whisper"]["service_url"].rstrip("/")
    language = cfg["whisper"].get("language", "zh")

    suspicious = [
        i for i, s in enumerate(segments)
        if s.get("avg_logprob", 0) < logprob_thresh
        or s.get("no_speech_prob", 0) > no_speech_thresh
    ]

    if not suspicious:
        log.info("verify_segments: all segments clean for %s", stem)
        return srt_path

    log.info("verify_segments: %d suspicious segments in %s", len(suspicious), stem)

    corrections: dict[int, str] = {}
    clip_path = audio_path.parent / f"{stem}_verify_clip.wav"

    for idx in suspicious:
        seg = segments[idx]
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(audio_path),
                 "-ss", str(seg["start"]), "-to", str(seg["end"]),
                 "-ar", "16000", "-ac", "1", str(clip_path)],
                check=True, capture_output=True, timeout=30,
            )
            with open(clip_path, "rb") as f:
                resp = httpx.post(
                    f"{service_url}/transcribe_large",
                    files={"audio": (clip_path.name, f)},
                    params={"language": language},
                    timeout=120.0,
                )
            resp.raise_for_status()
            result = resp.json()
            corrected = (result.get("text") or "").strip()
            original = seg["text"].strip()
            if corrected and corrected != original:
                corrections[idx] = corrected
                log.info("verify seg[%d]: %r → %r", idx, original[:40], corrected[:40])
        except Exception as exc:
            log.warning("verify seg[%d] failed: %s", idx, exc)

    clip_path.unlink(missing_ok=True)

    if not corrections:
        log.info("verify_segments: no corrections applied for %s", stem)
        return srt_path

    # Positional alignment: nonempty_segments[i] ↔ SRT block[i]
    blocks = _parse_srt_blocks(srt_path.read_text(encoding="utf-8", errors="replace"))
    lines = []
    for i, block in enumerate(blocks):
        text = corrections.get(i, block["text"])
        lines.append(f"{i + 1}\n{block['time']}\n{text}\n")
    srt_path.write_text("\n".join(lines), encoding="utf-8")

    log.info("verify_segments done: %s (%d corrections applied)", stem, len(corrections))
    return srt_path


# ── Stage 2c: Diarization helpers ───────────────────────────────────────────

def _assign_speaker(block_start: float, block_end: float, diar_segs: list) -> "str | None":
    """Return the diarization speaker with maximum overlap in [block_start, block_end]."""
    max_overlap = 0.0
    assigned = None
    for seg in diar_segs:
        overlap = min(block_end, seg["end"]) - max(block_start, seg["start"])
        if overlap > max_overlap:
            max_overlap = overlap
            assigned = seg["speaker"]
    return assigned


def diarize(stem: str, srt_path: Path, audio_path: Path, cfg: dict) -> Path:
    """Diarize audio via local service and enrich SRT with speaker labels.

    Passes Whisper segment timestamps to the service when {stem}_segments.json
    exists — avoids redundant VAD and uses Whisper's more accurate boundaries.
    Saves {stem}_diarization.json. Never raises.
    """
    diar_path = srt_path.parent / f"{stem}_diarization.json"
    d_cfg = cfg.get("diarization", {})
    service_url = d_cfg.get("service_url", "http://localhost:9003").rstrip("/")
    speaker_fmt = d_cfg.get("speaker_format", "【{speaker}】")
    speaker_names = d_cfg.get("speaker_names", {})
    num_speakers = d_cfg.get("num_speakers", None)

    if not audio_path.exists():
        log.warning("diarize: clean WAV not found for %s — skipping", stem)
        return diar_path

    seg_path = srt_path.parent / f"{stem}_segments.json"
    whisper_segs = None
    if seg_path.exists():
        raw = json.loads(seg_path.read_text(encoding="utf-8"))
        whisper_segs = [{"start": s["start"], "end": s["end"]} for s in raw]

    try:
        params = {"num_speakers": num_speakers} if num_speakers else {}
        data = {"segments": json.dumps(whisper_segs)} if whisper_segs else {}
        with open(audio_path, "rb") as f:
            resp = httpx.post(
                f"{service_url}/diarize",
                files={"audio": (audio_path.name, f)},
                params=params,
                data=data,
                timeout=600.0,
            )
        resp.raise_for_status()
    except httpx.ConnectError:
        log.warning("diarize: cannot reach service at %s — skipping", service_url)
        return diar_path
    except httpx.HTTPStatusError as exc:
        log.warning("diarize: service error %d — skipping", exc.response.status_code)
        return diar_path

    diar_segs = resp.json().get("segments", [])
    diar_path.write_text(json.dumps(diar_segs, ensure_ascii=False, indent=2), encoding="utf-8")

    if not diar_segs:
        log.info("diarize: no speaker segments returned for %s", stem)
        return diar_path

    srt_content = srt_path.read_text(encoding="utf-8", errors="replace")
    blocks = _parse_srt_blocks(srt_content)
    lines = []
    for i, block in enumerate(blocks):
        raw_speaker = _assign_speaker(
            _start_seconds(block["time"]),
            _end_seconds(block["time"]),
            diar_segs,
        )
        if raw_speaker:
            display = speaker_names.get(raw_speaker, raw_speaker)
            text = speaker_fmt.format(speaker=display) + block["text"]
        else:
            text = block["text"]
        lines.append(f"{i + 1}\n{block['time']}\n{text}\n")
    srt_path.write_text("\n".join(lines), encoding="utf-8")

    log.info("diarize done: %s (%d speaker segments)", stem, len(diar_segs))
    return diar_path


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


def _start_seconds(time_line: str) -> float:
    start = time_line.split("-->")[0].strip()
    h, m, rest = start.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def _fmt_duration(s: float) -> str:
    s = int(s)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


_COURSE_PATTERNS = {"lesson", "lecture", "class", "chapter", "tutorial", "session",
                    "上課", "課程", "教學", "課"}
_MEETING_PATTERNS = {"meeting", "standup", "review", "sync", "call", "debrief",
                     "會議", "週會", "例會", "討論"}


def _detect_recording_type(stem: str, cfg: dict) -> str:
    rtype = cfg.get("pipeline", {}).get("recording_type", "auto")
    if rtype != "auto":
        return rtype
    stem_lower = stem.lower()
    if any(p in stem_lower for p in _COURSE_PATTERNS):
        return "course"
    if any(p in stem_lower for p in _MEETING_PATTERNS):
        return "meeting"
    return "general"


def _ollama_chat(model: str, prompt: str) -> str:
    try:
        resp = _ollama.chat(model=model, messages=[{"role": "user", "content": prompt}])
        return resp["message"]["content"].strip()
    except Exception as exc:
        log.warning("Ollama unavailable: %s", exc)
        return ""


def correct_srt(stem: str, srt_path: Path, cfg: dict) -> Path:
    """Ollama correction pass: fix Whisper STT errors in-place. Never raises."""
    model = cfg["ollama"].get("model", "qwen2.5:7b")
    srt_content = srt_path.read_text(encoding="utf-8", errors="replace")
    blocks = _parse_srt_blocks(srt_content)
    if not blocks:
        return srt_path

    CHUNK = 40
    corrected_blocks: list[dict] = []

    for i in range(0, len(blocks), CHUNK):
        chunk = blocks[i:i + CHUNK]
        lines_in = "\n".join(f"{j}|{b['text']}" for j, b in enumerate(chunk))
        raw = _ollama_chat(model, PROMPTS["correct_srt"]["base"] + "\n" + lines_in)

        corrected_map: dict[int, str] = {}
        for line in raw.splitlines():
            if "|" in line:
                idx_str, _, text = line.partition("|")
                try:
                    corrected_map[int(idx_str.strip())] = text.strip()
                except ValueError:
                    pass

        for j, block in enumerate(chunk):
            corrected_blocks.append({
                "time": block["time"],
                "text": corrected_map.get(j, block["text"]),
            })

    lines = []
    for i, block in enumerate(corrected_blocks, start=1):
        lines.append(f"{i}\n{block['time']}\n{block['text']}\n")
    srt_path.write_text("\n".join(lines), encoding="utf-8")

    log.info("correct_srt done: %s (%d blocks)", stem, len(corrected_blocks))
    return srt_path


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
    rtype = _detect_recording_type(stem, cfg)
    p = PROMPTS["summarize"][rtype]

    # A. Overall summary
    overview = _ollama_chat(model, p["overview"] + "\n" + full_text[:6000])

    # B. Key moments (chunked for large files)
    chunk_size = 150
    all_moments: list[dict] = []
    for i in range(0, len(ts_lines), chunk_size):
        chunk = "\n".join(ts_lines[i:i + chunk_size])
        raw = _ollama_chat(model, p["moments"] + "\n" + chunk)
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
    topics_raw = _ollama_chat(model, p["topics"] + "\n" + "\n".join(sampled))
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
        f"> 音檔長度：{_fmt_duration(duration_s)} ｜ 類型：{rtype} ｜ 生成：{now} ｜ 模型：{model}",
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
        "recording_type": rtype,
        "summary": overview,
        "topic_segments": topic_segments,
        "key_moments": moments,
        "segment_count": len(blocks),
    }
    json_path.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")

    log.info("summarize done: %s → %s + %s", stem, md_path.name, json_path.name)
    return md_path


# ── Stage 4: Chapter Detection ───────────────────────────────────────────────

def detect_chapters(stem: str, srt_path: Path, output_dir: Path, cfg: dict) -> Path:
    """Detect chapter boundaries from silence gaps, title each via Ollama.

    Outputs {stem}_chapters.json. Appends chapter index to {stem}_summary.md
    if it already exists.
    Never raises.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    chapters_path = output_dir / f"{stem}_chapters.json"

    srt_content = srt_path.read_text(encoding="utf-8", errors="replace")
    blocks = _parse_srt_blocks(srt_content)

    if len(blocks) < 3:
        log.info("detect_chapters: too few blocks for %s", stem)
        chapters_path.write_text("[]", encoding="utf-8")
        return chapters_path

    ch_cfg = cfg.get("chapters", {})
    min_silence = float(ch_cfg.get("min_silence_sec", 30.0))
    max_chapters = int(ch_cfg.get("max_chapters", 8))
    model = cfg["ollama"].get("model", "qwen2.5:7b")

    # Detect silence gaps between consecutive segments
    gaps = []
    for i in range(len(blocks) - 1):
        gap = _start_seconds(blocks[i + 1]["time"]) - _end_seconds(blocks[i]["time"])
        if gap >= min_silence:
            gaps.append((i + 1, gap))

    # Keep the largest gaps up to max_chapters - 1 boundaries
    boundaries = sorted(gaps, key=lambda x: -x[1])[:max_chapters - 1]
    boundaries = sorted(boundaries, key=lambda x: x[0])  # restore time order

    chapter_start_indices = [0] + [b[0] for b in boundaries]

    chapters = []
    for ch_idx, blk_idx in enumerate(chapter_start_indices):
        start_sec = _start_seconds(blocks[blk_idx]["time"])
        start_fmt = _fmt_duration(start_sec)

        # Context: few lines around the boundary
        before = "\n".join(
            blocks[j]["text"]
            for j in range(max(0, blk_idx - 3), blk_idx)
        )
        after = "\n".join(
            blocks[j]["text"]
            for j in range(blk_idx, min(len(blocks), blk_idx + 3))
        )

        if ch_idx == 0:
            prompt = PROMPTS["chapters"]["first_title"] + "\n\n" + after
        else:
            prompt = (
                PROMPTS["chapters"]["boundary_title"] + "\n\n"
                + "【前段】\n" + before + "\n\n"
                + "【後段】\n" + after
            )

        title = _ollama_chat(model, prompt).strip()
        if not title or len(title) > 20:
            title = f"第{ch_idx + 1}段"

        chapters.append({
            "index": ch_idx,
            "start": start_fmt,
            "start_seconds": round(start_sec, 1),
            "title": title,
        })

    chapters_path.write_text(
        json.dumps(chapters, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # Append to summary.md if it exists
    md_path = output_dir / f"{stem}_summary.md"
    if md_path.exists() and chapters:
        with open(md_path, "a", encoding="utf-8") as f:
            f.write("\n## 章節索引\n")
            for ch in chapters:
                f.write(f"- `[{ch['start']}]` {ch['title']}\n")

    log.info("detect_chapters done: %s (%d chapters)", stem, len(chapters))
    return chapters_path

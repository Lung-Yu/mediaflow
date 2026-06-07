"""Pipeline stage runners — blocking, designed to run in a thread pool.

Each function performs one stage of the pipeline and returns the output path.
Raises on failure so the caller can publish task.failed and mark the file.
"""
import logging
import subprocess
from pathlib import Path
import httpx

log = logging.getLogger(__name__)


def preprocess(input_path: Path, workspace: Path, cfg: dict) -> Path:
    """Convert audio to 16 kHz mono WAV for Whisper."""
    proc_dir = workspace / "2_processing"
    proc_dir.mkdir(parents=True, exist_ok=True)
    out = proc_dir / f"{input_path.stem}.wav"

    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(input_path),
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
            str(out),
        ],
        check=True,
        capture_output=True,
    )
    log.info("Preprocessed %s → %s", input_path.name, out.name)
    return out


def transcribe(audio_path: Path, stem: str, output_dir: Path, cfg: dict) -> Path:
    """Call the Whisper HTTP service and save the resulting SRT."""
    output_dir.mkdir(parents=True, exist_ok=True)
    srt_path = output_dir / f"{stem}.srt"

    service_url = cfg["whisper"]["service_url"].rstrip("/")
    language = cfg["whisper"].get("language", "zh")

    with open(audio_path, "rb") as f:
        resp = httpx.post(
            f"{service_url}/inference",
            files={"file": (audio_path.name, f, "audio/wav")},
            data={"response_format": "srt", "language": language},
            timeout=3600.0,
        )
    resp.raise_for_status()

    srt_path.write_text(resp.text, encoding="utf-8")
    log.info("Transcribed %s → %s (%d bytes)", stem, srt_path.name, len(resp.content))
    return srt_path


def summarize(stem: str, srt_path: Path, output_dir: Path, cfg: dict) -> Path:
    """Call Ollama to generate a structured summary from the SRT transcript."""
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / f"{stem}_summary.txt"

    srt_text = srt_path.read_text(encoding="utf-8", errors="replace")
    # Truncate to avoid exceeding model context
    excerpt = srt_text[:12000]

    model = cfg["ollama"]["model"]
    ollama_url = cfg["ollama"]["service_url"].rstrip("/")

    prompt = (
        "以下是一段課程或會議錄音的逐字稿（SRT 格式）。"
        "請以繁體中文輸出結構化摘要：\n"
        "1. 主要主題\n"
        "2. 重點段落（各段一句話）\n"
        "3. 關鍵決策或行動項目\n\n"
        f"{excerpt}"
    )

    resp = httpx.post(
        f"{ollama_url}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=600.0,
    )
    resp.raise_for_status()

    summary_text = resp.json().get("response", "")
    summary_path.write_text(summary_text, encoding="utf-8")
    log.info("Summarized %s → %s", stem, summary_path.name)
    return summary_path

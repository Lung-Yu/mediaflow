"""SRT file parser."""
import re
from dataclasses import dataclass
from pathlib import Path

_BLOCK = re.compile(
    r"(\d+)\s*\n"
    r"(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,\.]\d{3})\s*\n"
    r"([\s\S]*?)(?=\n\n|\Z)",
    re.MULTILINE,
)


@dataclass
class Segment:
    index: int
    start: str
    end: str
    text: str


def parse(path: Path) -> list[Segment]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    segments = []
    for m in _BLOCK.finditer(raw):
        text = m.group(4).strip()
        if not text:
            continue
        segments.append(Segment(
            index=int(m.group(1)),
            start=m.group(2),
            end=m.group(3),
            text=text,
        ))
    return segments


def search(segments: list[Segment], query: str) -> list[Segment]:
    """Return segments whose text contains query (case-insensitive)."""
    if not query:
        return segments
    q = query.lower()
    return [s for s in segments if q in s.text.lower()]


def highlight(text: str, query: str) -> str:
    """Wrap every occurrence of query in <mark> tags."""
    if not query:
        return text
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    return pattern.sub(lambda m: f"<mark>{m.group()}</mark>", text)


def to_seconds(ts: str) -> float:
    """Convert SRT timestamp 'HH:MM:SS,mmm' or 'HH:MM:SS.mmm' to float seconds."""
    ts = ts.replace(",", ".")
    h, m, rest = ts.split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)

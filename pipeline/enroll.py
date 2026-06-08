"""Speaker enrollment CLI.

Extracts an ECAPA-TDNN embedding from an audio sample and stores it in the
speaker library so the diarization stage can identify the speaker by name.

Usage:
    python -m pipeline.enroll --name 老師 --audio sample.wav
    python -m pipeline.enroll --list
    python -m pipeline.enroll --remove 老師

The library is stored in data/speaker_library.json (relative to the project root).
The diarize service at localhost:9003 must be running to enroll a speaker.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

LIBRARY_PATH = Path("data/speaker_library.json")
DEFAULT_SERVICE = "http://localhost:9003"


def _load_library() -> list:
    if not LIBRARY_PATH.exists():
        return []
    return json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))


def _save_library(entries: list) -> None:
    LIBRARY_PATH.parent.mkdir(parents=True, exist_ok=True)
    LIBRARY_PATH.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def cmd_list(args) -> int:
    entries = _load_library()
    if not entries:
        print("Speaker library is empty.")
        return 0
    print(f"{'Name':<20}  {'Added'}")
    print("-" * 45)
    for e in entries:
        print(f"{e['name']:<20}  {e.get('added_at', 'unknown')}")
    return 0


def cmd_remove(args) -> int:
    entries = _load_library()
    before = len(entries)
    entries = [e for e in entries if e["name"] != args.remove]
    if len(entries) == before:
        print(f"Speaker '{args.remove}' not found in library.", file=sys.stderr)
        return 1
    _save_library(entries)
    print(f"Removed '{args.remove}' from speaker library.")
    return 0


def cmd_enroll(args) -> int:
    audio_path = Path(args.audio)
    if not audio_path.exists():
        print(f"Audio file not found: {audio_path}", file=sys.stderr)
        return 1

    service_url = (args.service or DEFAULT_SERVICE).rstrip("/")
    print(f"Extracting embedding from {audio_path.name} via {service_url}/embed ...")

    try:
        with open(audio_path, "rb") as f:
            resp = httpx.post(
                f"{service_url}/embed",
                files={"audio": (audio_path.name, f)},
                timeout=120.0,
            )
        resp.raise_for_status()
    except httpx.ConnectError:
        print(f"Cannot reach diarize service at {service_url}. Is it running?", file=sys.stderr)
        print("Start it with: bash scripts/start-diarize.sh", file=sys.stderr)
        return 1
    except httpx.HTTPStatusError as exc:
        print(f"Service error {exc.response.status_code}: {exc.response.text}", file=sys.stderr)
        return 1

    data = resp.json()
    if "error" in data:
        print(f"Embedding failed: {data['error']}", file=sys.stderr)
        return 1

    embedding = data["embedding"]
    entries = _load_library()

    existing = next((e for e in entries if e["name"] == args.name), None)
    if existing:
        if not args.force:
            print(
                f"Speaker '{args.name}' already enrolled. Use --force to overwrite.",
                file=sys.stderr,
            )
            return 1
        existing["embedding"] = embedding
        existing["added_at"] = datetime.now(timezone.utc).isoformat()
        existing["source"] = str(audio_path)
        print(f"Updated embedding for '{args.name}'.")
    else:
        entries.append({
            "name": args.name,
            "embedding": embedding,
            "added_at": datetime.now(timezone.utc).isoformat(),
            "source": str(audio_path),
        })
        print(f"Enrolled '{args.name}' ({len(embedding)}-dim embedding).")

    _save_library(entries)
    print(f"Library saved to {LIBRARY_PATH} ({len(entries)} speaker(s)).")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Enroll speakers for mediaflow diarization.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--name", help="Speaker display name to enroll")
    parser.add_argument("--audio", help="Audio sample file (WAV, M4A, MP3 etc.)")
    parser.add_argument("--list", action="store_true", help="List enrolled speakers")
    parser.add_argument("--remove", metavar="NAME", help="Remove a speaker from the library")
    parser.add_argument("--force", action="store_true", help="Overwrite existing enrollment")
    parser.add_argument("--service", default=DEFAULT_SERVICE, help="Diarize service URL")
    args = parser.parse_args()

    if args.list:
        return cmd_list(args)
    if args.remove:
        return cmd_remove(args)
    if args.name and args.audio:
        return cmd_enroll(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())

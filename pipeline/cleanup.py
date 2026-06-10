"""One-shot lifecycle cleanup — mirrors what the watcher's hourly poller does.

Usage:
    python -m pipeline.cleanup [--dry-run] [--target wav|archive|output|all]
"""
import argparse
import logging
from pathlib import Path

from pipeline.config import load
from pipeline.lifecycle import parse_retention, scan_and_expire


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply lifecycle policy to workspace data")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be deleted without deleting")
    parser.add_argument(
        "--target",
        choices=["wav", "archive", "output", "all"],
        default="all",
        help="Which data tier to clean (default: all)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load()
    ws = Path(cfg["pipeline"]["workspace_dir"])
    lc = cfg.get("lifecycle", {})

    deleted = []

    if args.target in ("wav", "all"):
        ret = parse_retention(lc.get("wav", "keep"))
        if ret is not None and ret.total_seconds() > 0:
            deleted += scan_and_expire(ws / "2_processing", ret, "*_clean.wav", args.dry_run)

    if args.target in ("archive", "all"):
        ret = parse_retention(lc.get("archive", "forever"))
        if ret is not None and ret.total_seconds() > 0:
            deleted += scan_and_expire(ws / "4_archive", ret, "*", args.dry_run)

    if args.target in ("output", "all"):
        ret = parse_retention(lc.get("output", "forever"))
        if ret is not None and ret.total_seconds() > 0:
            deleted += scan_and_expire(ws / "3_output", ret, "*", args.dry_run)

    prefix = "[dry-run] Would delete" if args.dry_run else "Deleted"
    print(f"{prefix} {len(deleted)} file(s)")


if __name__ == "__main__":
    main()

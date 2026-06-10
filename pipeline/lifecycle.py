"""Lifecycle helpers — parse retention config and expire files by age."""
import logging
import time
from datetime import timedelta
from pathlib import Path

log = logging.getLogger(__name__)


def parse_retention(value: str) -> "timedelta | None":
    """Parse a retention string into a timedelta or None (keep forever).

    "immediate" → timedelta(0), "Nd" → timedelta(N days), "forever"/"keep"/"" → None.
    """
    if not value or value in ("forever", "keep"):
        return None
    if value == "immediate":
        return timedelta(0)
    if isinstance(value, str) and value.endswith("d"):
        try:
            return timedelta(days=int(value[:-1]))
        except ValueError:
            pass
    log.warning("Unrecognised retention value %r — treating as forever", value)
    return None


def safe_unlink(path: Path, label: str = "") -> bool:
    """Delete path. Returns True if deleted, False if already gone or other error."""
    try:
        path.unlink()
        log.info("Deleted %s: %s", label, path.name)
        return True
    except FileNotFoundError:
        log.debug("Already gone %s: %s", label, path.name)
        return False
    except OSError as exc:
        log.warning("Could not delete %s %s: %s", label, path.name, exc)
        return False


def scan_and_expire(
    directory: Path,
    retention: "timedelta | None",
    stem_pattern: str = "*",
    dry_run: bool = False,
) -> list:
    """Delete files in directory older than retention. Returns list of deleted (or would-delete) paths.

    retention=None means keep forever. retention=timedelta(0) means delete all matching files.
    FileNotFoundError on any individual file is caught and logged at DEBUG — does not stop the scan.
    """
    if retention is None:
        return []
    if not directory.exists():
        log.debug("scan_and_expire: directory does not exist: %s", directory)
        return []

    cutoff = time.time() - retention.total_seconds()
    deleted = []

    for f in directory.glob(stem_pattern):
        if not f.is_file():
            continue
        try:
            mtime = f.stat().st_mtime
        except FileNotFoundError:
            log.debug("Already gone during scan: %s", f.name)
            continue
        if mtime <= cutoff:
            if dry_run:
                log.info("[dry-run] Would delete: %s", f)
                deleted.append(f)
            elif safe_unlink(f, "expired"):
                deleted.append(f)

    return deleted

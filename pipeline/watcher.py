"""File watcher — monitors 1_input/, uploads to MinIO, notifies Project Service.

On startup: re-ingests any files left in 1_input/ from a previous restart.
On new file: strips quarantine attrs, uploads to MinIO input/, POSTs to /jobs.
On error: renames file to .failed so it is skipped on next restart.
"""
from __future__ import annotations
import logging
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers.polling import PollingObserver

from pipeline.config import load, workspace
from pipeline.lifecycle import parse_retention, scan_and_expire

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="watcher")
_API_URL = os.getenv("API_URL", "http://localhost:8080")


def _ingest_file(path: Path, cfg: dict) -> None:
    """Upload file to MinIO input/ and notify Project Service via POST /jobs."""
    from api.utils.minio import get_client
    filename = path.name
    minio_key = f"input/{filename}"

    try:
        client = get_client()
        client.upload_file(minio_key, path, bucket=client.input_bucket)
        log.info("Uploaded %s → MinIO %s", filename, minio_key)
    except Exception as exc:
        log.error("MinIO upload failed for %s: %s", filename, exc)
        _mark_failed(path)
        return

    try:
        resp = httpx.post(
            f"{_API_URL}/jobs",
            json={"file_key": minio_key, "filename": filename},
            timeout=30.0,
        )
        resp.raise_for_status()
        job_id = resp.json().get("job_id")
        log.info("Submitted %s → job_id=%s", filename, job_id)
        path.unlink(missing_ok=True)
    except Exception as exc:
        log.error("API notify failed for %s: %s", filename, exc)
        _mark_failed(path)


def _mark_failed(path: Path) -> None:
    failed = path.with_suffix(path.suffix + ".failed")
    try:
        path.rename(failed)
        log.warning("Marked failed: %s → %s", path.name, failed.name)
    except OSError as exc:
        log.error("Could not rename %s to .failed: %s", path.name, exc)


def _lifecycle_poller(cfg: dict, ws: Path, stop_event: threading.Event) -> None:
    """Hourly scan of 2_processing/ and 4_archive/ for time-based retention rules."""
    log.info("Lifecycle poller started")
    while not stop_event.is_set():
        try:
            lc = cfg.get("lifecycle", {})
            wav_ret = parse_retention(lc.get("wav", "keep"))
            arch_ret = parse_retention(lc.get("archive", "forever"))
            if wav_ret is not None and wav_ret.total_seconds() > 0:
                scan_and_expire(ws / "2_processing", wav_ret, stem_pattern="*_clean.wav")
            if arch_ret is not None and arch_ret.total_seconds() > 0:
                scan_and_expire(ws / "4_archive", arch_ret)
        except Exception as exc:
            log.warning("Lifecycle poller error: %s", exc)
        stop_event.wait(3600)
    log.info("Lifecycle poller stopped")


class InputHandler(FileSystemEventHandler):
    def __init__(self, cfg: dict):
        self._cfg = cfg
        self._formats = set(cfg["pipeline"]["supported_formats"])

    def on_created(self, event: FileCreatedEvent):
        if event.is_directory:
            return
        path = Path(event.src_path)
        if path.name.endswith(".failed"):
            return
        if path.suffix not in self._formats:
            return
        self._submit(path)

    def _submit(self, path: Path):
        try:
            subprocess.run(["xattr", "-c", str(path)], capture_output=True)
        except FileNotFoundError:
            pass
        log.info("Detected %s", path.name)
        _executor.submit(_ingest_file, path, self._cfg)


def run():
    cfg = load()
    input_dir = workspace(cfg, "1_input")
    input_dir.mkdir(parents=True, exist_ok=True)

    from api.utils.minio import init_client
    try:
        init_client()
    except Exception as exc:
        log.warning("MinIO init failed (will retry per-file): %s", exc)

    stop_ev = threading.Event()
    lc_poller = threading.Thread(
        target=_lifecycle_poller,
        args=(cfg, Path(cfg["pipeline"]["workspace_dir"]), stop_ev),
        daemon=True,
        name="lifecycle-poller",
    )
    lc_poller.start()

    handler = InputHandler(cfg)
    observer = PollingObserver(timeout=5)
    observer.schedule(handler, str(input_dir), recursive=False)
    observer.start()
    log.info("Watching %s", input_dir)

    # Recover files left from previous run
    for f in sorted(input_dir.iterdir()):
        if f.name.endswith(".failed"):
            continue
        if f.suffix in cfg["pipeline"]["supported_formats"]:
            log.info("Recovering on startup: %s", f.name)
            handler._submit(f)

    try:
        while observer.is_alive():
            observer.join(timeout=1)
    except KeyboardInterrupt:
        stop_ev.set()
        observer.stop()

    observer.join()
    _executor.shutdown(wait=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()

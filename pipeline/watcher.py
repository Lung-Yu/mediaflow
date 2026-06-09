"""File watcher — monitors 1_input/ and runs the full pipeline for each new file.

On startup: scans 1_input/ to recover from restart without move-out/back workarounds.
On new file: strips quarantine attrs, then runs pipeline stages via runner.execute().
On error: renames file to .failed so it is skipped on next restart.
"""
import logging
import os
import sqlite3
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from pipeline.config import load, workspace
from pipeline.mq.publisher import EventPublisher
from pipeline import runner

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline")


def _run_pipeline(path: Path, cfg: dict, pub: EventPublisher) -> None:
    """Run all configured stages for a single file. Called in a worker thread."""
    stem = path.stem
    ws = Path(cfg["pipeline"]["workspace_dir"])

    ctx = {
        "stem": stem,
        "input_path": path,
        "workspace": ws,
        "output_dir": ws / "3_output",
        "audio_path": ws / "2_processing" / f"{stem}_clean.wav",
        "srt_path": ws / "3_output" / f"{stem}.srt",
    }

    try:
        ctx = runner.execute(cfg, ctx, pub)

        archive_dir = ws / "4_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        path.rename(archive_dir / path.name)
        pub.publish("task.completed", stem, output_path=str(ctx["srt_path"]))
        log.info("DONE %s", stem)

    except Exception as exc:
        log.error("Pipeline FAILED for %s: %s", stem, exc)
        pub.publish("task.failed", stem, error_msg=str(exc))
        _mark_failed(path)


def _mark_failed(path: Path) -> None:
    failed = path.with_suffix(path.suffix + ".failed")
    try:
        path.rename(failed)
        log.warning("Marked failed: %s → %s", path.name, failed.name)
    except OSError as exc:
        log.error("Could not rename %s to .failed: %s", path.name, exc)


def _run_rerun(stem: str, from_stage: "str | None", cfg: dict, pub: EventPublisher) -> None:
    """Execute a rerun command dispatched from the reruns DB table."""
    from pipeline.rerun import rerun
    try:
        rerun(stem, from_stage or "preprocess", cfg, pub)
    except Exception as exc:
        log.error("Rerun FAILED for %s: %s", stem, exc)
        pub.publish("task.failed", stem, error_msg=str(exc))


def _rerun_poller(
    cfg: dict,
    pub: EventPublisher,
    db_path: str,
    stop_event: threading.Event,
) -> None:
    """Poll the reruns table every 2 s and dispatch work to the thread pool."""
    log.info("Rerun poller started (db=%s)", db_path)
    conn = None
    while not stop_event.is_set():
        try:
            if conn is None:
                conn = sqlite3.connect(db_path)
                conn.row_factory = sqlite3.Row
            row = None
            with conn:
                cur = conn.execute(
                    "SELECT * FROM reruns ORDER BY requested_at ASC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    conn.execute("DELETE FROM reruns WHERE id = ?", (row["id"],))
            if row:
                log.info("Rerun queued: stem=%s from_stage=%s", row["stem"], row["from_stage"])
                _executor.submit(_run_rerun, row["stem"], row["from_stage"], cfg, pub)
        except Exception as exc:
            log.warning("Rerun poller: %s — will retry", exc)
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
                conn = None
        time.sleep(2)
    if conn is not None:
        conn.close()
    log.info("Rerun poller stopped")


class InputHandler(FileSystemEventHandler):
    def __init__(self, cfg: dict, publisher: EventPublisher):
        self._cfg = cfg
        self._pub = publisher
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
        # Strip macOS quarantine attrs; no-op on Linux
        try:
            subprocess.run(["xattr", "-c", str(path)], capture_output=True)
        except FileNotFoundError:
            pass

        stem = path.stem
        log.info("SUBMIT %s", path.name)
        self._pub.publish("task.submitted", stem, filename=path.name)
        _executor.submit(_run_pipeline, path, self._cfg, self._pub)


def run():
    cfg = load()
    pub = EventPublisher(cfg)
    input_dir = workspace(cfg, "1_input")
    input_dir.mkdir(parents=True, exist_ok=True)

    db_path = os.getenv("DB_PATH", "./data/pipeline.db")
    stop_ev = threading.Event()
    poller = threading.Thread(
        target=_rerun_poller,
        args=(cfg, pub, db_path, stop_ev),
        daemon=True,
        name="rerun-poller",
    )
    poller.start()

    handler = InputHandler(cfg, pub)
    observer = Observer()
    observer.schedule(handler, str(input_dir), recursive=False)
    observer.start()

    log.info("Watching %s", input_dir)

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

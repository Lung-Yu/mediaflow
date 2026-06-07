"""File watcher — monitors 1_input/ and publishes events via Redis Streams.

On startup: scans 1_input/ for existing files to recover from restart.
On new file: validates, strips quarantine attrs, publishes task.submitted.
On error: renames file to .failed so it is skipped on next restart.
"""
import logging
import subprocess
import time
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from pipeline.config import load, workspace
from pipeline.mq.publisher import EventPublisher

log = logging.getLogger(__name__)


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
        self._process(path)

    def _process(self, path: Path):
        # Wait for write to complete before reading
        time.sleep(2)
        if not path.exists():
            return

        stem = path.stem
        log.info("START %s", path.name)

        # Strip macOS quarantine attrs; no-op on Linux
        try:
            subprocess.run(["xattr", "-c", str(path)], capture_output=True)
        except FileNotFoundError:
            pass

        try:
            self._pub.publish("task.submitted", stem, filename=path.name)
        except Exception as exc:
            log.error("Failed to publish event for %s: %s", path.name, exc)
            self._mark_failed(path, str(exc))

    def _mark_failed(self, path: Path, reason: str):
        """Rename to .failed so watcher skips it on restart, avoiding infinite retry."""
        failed_path = path.with_suffix(path.suffix + ".failed")
        try:
            path.rename(failed_path)
            log.warning("Marked as failed: %s → %s (%s)", path.name, failed_path.name, reason)
        except OSError as exc:
            log.error("Could not rename %s to .failed: %s", path.name, exc)


def run():
    cfg = load()
    pub = EventPublisher(cfg)
    input_dir = workspace(cfg, "1_input")
    input_dir.mkdir(parents=True, exist_ok=True)

    handler = InputHandler(cfg, pub)
    observer = Observer()
    observer.schedule(handler, str(input_dir), recursive=False)
    observer.start()

    log.info("Watching %s", input_dir)

    # Recover existing files on startup (handles restart without move-out/back workaround)
    for f in sorted(input_dir.iterdir()):
        if f.name.endswith(".failed"):
            continue
        if f.suffix in cfg["pipeline"]["supported_formats"]:
            log.info("Recovering on startup: %s", f.name)
            handler._process(f)

    try:
        while observer.is_alive():
            observer.join(timeout=1)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()

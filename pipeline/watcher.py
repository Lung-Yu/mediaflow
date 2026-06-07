"""File watcher — drop-in replacement for existing pipeline watcher.

On startup: scans 1_input/ for existing files to recover from restart.
On new file: validates, strips quarantine attrs, queues for processing.
"""
import logging
import os
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
        if path.suffix not in self._formats:
            return
        if path.suffix == ".failed":
            return
        self._process(path)

    def _process(self, path: Path):
        # Wait for write to complete
        time.sleep(2)
        if not path.exists():
            return

        stem = path.stem
        log.info(f"START {path.name}")
        self._pub.publish("task.submitted", stem, filename=path.name)

        # Strip macOS quarantine (no-op on Linux)
        try:
            subprocess.run(["xattr", "-c", str(path)], capture_output=True)
        except FileNotFoundError:
            pass  # xattr not available (Linux)

        # Mark failed on unrecoverable error
        # Actual stage execution to be wired in next iteration


def run():
    cfg = load()
    pub = EventPublisher(cfg)
    input_dir = workspace(cfg, "1_input")
    input_dir.mkdir(parents=True, exist_ok=True)

    handler = InputHandler(cfg, pub)
    observer = Observer()
    observer.schedule(handler, str(input_dir), recursive=False)
    observer.start()

    log.info(f"Watching {input_dir}")

    # Recover existing files on startup
    for f in input_dir.iterdir():
        if f.suffix in cfg["pipeline"]["supported_formats"] and not f.name.endswith(".failed"):
            log.info(f"Recovering existing file: {f.name}")
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

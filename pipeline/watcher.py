"""File watcher — monitors 1_input/ and runs the full pipeline for each new file.

On startup: scans 1_input/ to recover from restart without move-out/back workarounds.
On new file: strips quarantine attrs, then runs preprocessing → transcription → summary
             in a thread pool so the watcher loop stays responsive.
On error: renames file to .failed so it is skipped on next restart.
"""
import logging
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from watchdog.events import FileCreatedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from pipeline.config import load, workspace
from pipeline.mq.publisher import EventPublisher
from pipeline import stages

log = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="pipeline")


def _run_pipeline(path: Path, cfg: dict, pub: EventPublisher) -> None:
    """Run all stages for a single file. Called in a worker thread."""
    stem = path.stem
    ws = Path(cfg["pipeline"]["workspace_dir"])
    output_dir = ws / "3_output"
    archive_dir = ws / "4_archive"

    try:
        # Stage 1: preprocessing
        audio_path = stages.preprocess(path, ws, cfg)
        pub.publish("stage.completed", stem, stage="preprocessing", filename=path.name)

        # Stage 2: transcription
        srt_path = stages.transcribe(audio_path, stem, output_dir, cfg)
        pub.publish("stage.completed", stem, stage="transcription", output_path=str(srt_path))

        if cfg.get("pipeline", {}).get("llm_correction", False):
            stages.correct_srt(stem, srt_path, cfg)

        # Stage 3: summary
        stages.summarize(stem, srt_path, output_dir, cfg)
        pub.publish("stage.completed", stem, stage="summary")

        # Done — move input to archive
        archive_dir.mkdir(parents=True, exist_ok=True)
        path.rename(archive_dir / path.name)
        pub.publish("task.completed", stem, output_path=str(srt_path))
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

    handler = InputHandler(cfg, pub)
    observer = Observer()
    observer.schedule(handler, str(input_dir), recursive=False)
    observer.start()

    log.info("Watching %s", input_dir)

    # Recover files already in 1_input/ (handles restart)
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
        observer.stop()

    observer.join()
    _executor.shutdown(wait=False)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run()

"""Load prompt templates from pipeline/prompts.yaml.

Loaded once at import time. Restart watcher/rerun to pick up changes.
For evaluation iteration, each `python -m pipeline.rerun` is a fresh process.
"""
from pathlib import Path
import yaml

_PATH = Path(__file__).parent / "prompts.yaml"


def load() -> dict:
    with open(_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


PROMPTS: dict = load()

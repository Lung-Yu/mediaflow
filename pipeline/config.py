import yaml
from pathlib import Path


def load(path: str = "config.yaml") -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def workspace(cfg: dict, subdir: str) -> Path:
    base = Path(cfg["pipeline"]["workspace_dir"])
    return base / subdir

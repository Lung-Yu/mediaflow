from __future__ import annotations

import json
import httpx
from pathlib import Path
from typing import Optional

from .base import DiarizeProvider


class SpeechbrainDiarizeProvider(DiarizeProvider):
    def __init__(self, config: dict):
        self.service_url = config.get("service_url", "http://localhost:9003")
        self.speaker_format = config.get("speaker_format", "【{label}】")
        self.timeout = float(config.get("timeout_sec", 600))

    def diarize(
        self, audio_path: Path, segments: list[dict], num_speakers: Optional[int]
    ) -> list[dict]:
        with open(audio_path, "rb") as f:
            data: dict = {"segments": json.dumps([
                {"start": s["start"], "end": s["end"]} for s in segments
            ])}
            if num_speakers is not None:
                data["num_speakers"] = str(num_speakers)
            resp = httpx.post(
                f"{self.service_url}/diarize",
                files={"audio": (audio_path.name, f)},
                data=data,
                timeout=self.timeout,
            )
        resp.raise_for_status()
        return resp.json()["segments"]


class PyannoteDiarizeProvider(DiarizeProvider):
    """Uses pyannote.audio pipeline locally (requires HF_TOKEN)."""

    def __init__(self, config: dict):
        import os
        self.hf_token = os.getenv("HF_TOKEN", config.get("hf_token", ""))
        self.speaker_format = config.get("speaker_format", "【{label}】")
        self._pipeline = None

    def _load(self) -> None:
        if self._pipeline is None:
            from pyannote.audio import Pipeline
            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=self.hf_token,
            )

    def diarize(
        self, audio_path: Path, segments: list[dict], num_speakers: Optional[int]
    ) -> list[dict]:
        self._load()
        kwargs: dict = {}
        if num_speakers is not None:
            kwargs["num_speakers"] = num_speakers
        diarization = self._pipeline(str(audio_path), **kwargs)
        result = []
        for turn, _, speaker in diarization.itertracks(yield_label=True):
            result.append({
                "speaker": speaker,
                "start": round(turn.start, 3),
                "end": round(turn.end, 3),
            })
        return result

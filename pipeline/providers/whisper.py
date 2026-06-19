from __future__ import annotations

import httpx
from pathlib import Path

from .base import WhisperProvider


class _HttpWhisperProvider(WhisperProvider):
    """Base for providers using the Whisper HTTP service (shared HTTP API)."""

    def __init__(self, config: dict):
        self.service_url = config.get("service_url", "http://localhost:9001")
        self.language = config.get("language", "zh")
        self.model = config.get("model", "medium")
        self.initial_prompt = config.get("initial_prompt", "")
        self.timeout = float(config.get("timeout_sec", 1800))

    def transcribe_segments(self, audio_path: Path, language: str) -> list[dict]:
        with open(audio_path, "rb") as f:
            params: dict = {"language": language or self.language}
            if self.initial_prompt:
                params["initial_prompt"] = self.initial_prompt
            resp = httpx.post(
                f"{self.service_url}/transcribe_segments",
                files={"audio": (audio_path.name, f)},
                params=params,
                timeout=self.timeout,
            )
        resp.raise_for_status()
        return resp.json()["segments"]

    def transcribe_large(self, audio_path: Path, language: str) -> list[dict]:
        """Uses /transcribe_large endpoint (whisper-large-v3)."""
        with open(audio_path, "rb") as f:
            resp = httpx.post(
                f"{self.service_url}/transcribe_large",
                files={"audio": (audio_path.name, f)},
                params={"language": language or self.language},
                timeout=self.timeout,
            )
        resp.raise_for_status()
        return resp.json()["segments"]


class MlxWhisperProvider(_HttpWhisperProvider):
    """mlx-whisper backend — Apple Silicon GPU, http service on :9001."""


class FasterWhisperProvider(_HttpWhisperProvider):
    """faster-whisper backend — CPU/CUDA, http service on :9001."""


class OpenAIWhisperProvider(WhisperProvider):
    def __init__(self, config: dict):
        import os
        import openai
        self.client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
        self.model = config.get("model", "whisper-1")
        self.language = config.get("language", "zh")

    def transcribe_segments(self, audio_path: Path, language: str) -> list[dict]:
        with open(audio_path, "rb") as f:
            transcript = self.client.audio.transcriptions.create(
                model=self.model,
                file=f,
                language=language or self.language,
                response_format="verbose_json",
                timestamp_granularities=["segment"],
            )
        return [
            {
                "id": i,
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "avg_logprob": -0.3,
                "no_speech_prob": 0.0,
            }
            for i, s in enumerate(transcript.segments or [])
        ]

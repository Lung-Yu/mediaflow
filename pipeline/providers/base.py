from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class WhisperProvider(ABC):
    @abstractmethod
    def transcribe_segments(self, audio_path: Path, language: str) -> list[dict]:
        """Return segments: [{id, start, end, text, avg_logprob, no_speech_prob}]"""

    def transcribe_large(self, audio_path: Path, language: str) -> list[dict]:
        """Verify-quality transcription (large model). Default: calls transcribe_segments."""
        return self.transcribe_segments(audio_path, language)


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, prompt: str) -> str:
        """Send prompt, return text response."""


class DiarizeProvider(ABC):
    @abstractmethod
    def diarize(
        self,
        audio_path: Path,
        segments: list[dict],
        num_speakers: Optional[int],
    ) -> list[dict]:
        """Return [{speaker, start, end}] aligned to input segments."""

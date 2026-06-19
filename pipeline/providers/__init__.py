from __future__ import annotations

from .base import WhisperProvider, LLMProvider, DiarizeProvider
from .whisper import MlxWhisperProvider, FasterWhisperProvider, OpenAIWhisperProvider
from .llm import OllamaLLMProvider, OpenAILLMProvider
from .diarize import SpeechbrainDiarizeProvider, PyannoteDiarizeProvider

__all__ = [
    "WhisperProvider", "LLMProvider", "DiarizeProvider",
    "get_whisper_provider", "get_llm_provider", "get_diarize_provider",
]

_WHISPER_PROVIDERS = {
    "mlx-whisper":    MlxWhisperProvider,
    "faster-whisper": FasterWhisperProvider,
    "openai":         OpenAIWhisperProvider,
}

_LLM_PROVIDERS = {
    "ollama": OllamaLLMProvider,
    "openai": OpenAILLMProvider,
}

_DIARIZE_PROVIDERS = {
    "speechbrain": SpeechbrainDiarizeProvider,
    "pyannote":    PyannoteDiarizeProvider,
}


def get_whisper_provider(config: dict) -> WhisperProvider:
    key = config.get("provider", "mlx-whisper")
    cls = _WHISPER_PROVIDERS.get(key)
    if not cls:
        raise ValueError(
            f"Unknown whisper provider: {key!r}. "
            f"Valid: {list(_WHISPER_PROVIDERS)}"
        )
    return cls(config)


def get_llm_provider(config: dict) -> LLMProvider:
    key = config.get("provider", "ollama")
    cls = _LLM_PROVIDERS.get(key)
    if not cls:
        raise ValueError(
            f"Unknown LLM provider: {key!r}. "
            f"Valid: {list(_LLM_PROVIDERS)}"
        )
    return cls(config)


def get_diarize_provider(config: dict) -> DiarizeProvider:
    key = config.get("provider", "speechbrain")
    cls = _DIARIZE_PROVIDERS.get(key)
    if not cls:
        raise ValueError(
            f"Unknown diarize provider: {key!r}. "
            f"Valid: {list(_DIARIZE_PROVIDERS)}"
        )
    return cls(config)

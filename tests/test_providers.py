from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── Task 1: ABC and factory tests ───────────────────────────────────────────

def test_whisper_provider_is_abstract():
    from pipeline.providers.base import WhisperProvider
    with pytest.raises(TypeError):
        WhisperProvider()


def test_llm_provider_is_abstract():
    from pipeline.providers.base import LLMProvider
    with pytest.raises(TypeError):
        LLMProvider()


def test_diarize_provider_is_abstract():
    from pipeline.providers.base import DiarizeProvider
    with pytest.raises(TypeError):
        DiarizeProvider()


def test_get_whisper_provider_mlx(monkeypatch):
    from pipeline.providers import get_whisper_provider
    from pipeline.providers.whisper import MlxWhisperProvider
    p = get_whisper_provider({"provider": "mlx-whisper", "language": "zh", "model": "medium"})
    assert isinstance(p, MlxWhisperProvider)


def test_get_whisper_provider_unknown_raises():
    from pipeline.providers import get_whisper_provider
    with pytest.raises(ValueError, match="Unknown whisper provider"):
        get_whisper_provider({"provider": "does-not-exist"})


def test_get_llm_provider_ollama():
    from pipeline.providers import get_llm_provider
    from pipeline.providers.llm import OllamaLLMProvider
    p = get_llm_provider({"provider": "ollama", "model": "qwen2.5:7b"})
    assert isinstance(p, OllamaLLMProvider)


def test_get_diarize_provider_speechbrain():
    from pipeline.providers import get_diarize_provider
    from pipeline.providers.diarize import SpeechbrainDiarizeProvider
    p = get_diarize_provider({"provider": "speechbrain", "num_speakers": None})
    assert isinstance(p, SpeechbrainDiarizeProvider)


# ── Task 2: MlxWhisperProvider implementation tests ─────────────────────────

@pytest.fixture
def audio_path(tmp_path):
    p = tmp_path / "test.wav"
    p.write_bytes(b"fake-wav")
    return p


def test_mlx_whisper_transcribe_segments_calls_service(audio_path):
    from pipeline.providers.whisper import MlxWhisperProvider
    cfg = {"service_url": "http://localhost:9001", "language": "zh", "model": "medium"}
    provider = MlxWhisperProvider(cfg)
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {"segments": [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "你好",
         "avg_logprob": -0.3, "no_speech_prob": 0.1}
    ]}
    with patch("httpx.post", return_value=fake_resp) as mock_post:
        result = provider.transcribe_segments(audio_path, "zh")
    assert mock_post.called
    assert result[0]["text"] == "你好"


def test_mlx_whisper_raises_on_http_error(audio_path):
    from pipeline.providers.whisper import MlxWhisperProvider
    cfg = {"service_url": "http://localhost:9001", "language": "zh", "model": "medium"}
    provider = MlxWhisperProvider(cfg)
    fake_resp = MagicMock()
    fake_resp.status_code = 500
    fake_resp.text = "internal error"
    fake_resp.raise_for_status.side_effect = Exception("HTTP 500")
    with patch("httpx.post", return_value=fake_resp):
        with pytest.raises(Exception):
            provider.transcribe_segments(audio_path, "zh")


# ── Task 3: OllamaLLMProvider + OpenAILLMProvider tests ─────────────────────

def test_ollama_llm_calls_chat(monkeypatch):
    from pipeline.providers.llm import OllamaLLMProvider
    mock_resp = {"message": {"content": "這是摘要"}}
    import ollama as _ollama
    fake_client = MagicMock()
    fake_client.chat.return_value = mock_resp
    monkeypatch.setattr(_ollama, "Client", lambda **kw: fake_client)
    p = OllamaLLMProvider({"model": "qwen2.5:7b"})
    result = p.chat("summarize this")
    assert result == "這是摘要"


def test_openai_llm_calls_completions(monkeypatch):
    openai = pytest.importorskip("openai", reason="openai package not installed")
    from pipeline.providers.llm import OpenAILLMProvider
    fake_completion = MagicMock()
    fake_completion.choices[0].message.content = "summary text"
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_completion
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: fake_client)
    p = OpenAILLMProvider({"model": "gpt-4o-mini"})
    result = p.chat("summarize this")
    assert result == "summary text"


# ── Task 4: SpeechbrainDiarizeProvider tests ─────────────────────────────────

def test_speechbrain_diarize_calls_service(audio_path):
    from pipeline.providers.diarize import SpeechbrainDiarizeProvider
    segments = [{"start": 0.0, "end": 2.0}]
    fake_resp = MagicMock()
    fake_resp.status_code = 200
    fake_resp.json.return_value = {
        "segments": [{"speaker": "SPEAKER_00", "start": 0.0, "end": 2.0}]
    }
    with patch("httpx.post", return_value=fake_resp):
        p = SpeechbrainDiarizeProvider({"service_url": "http://localhost:9003"})
        result = p.diarize(audio_path, segments, num_speakers=None)
    assert result[0]["speaker"] == "SPEAKER_00"

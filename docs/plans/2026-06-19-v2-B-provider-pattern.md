# Sub-plan B: Provider Pattern

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce a provider abstraction so each pipeline stage selects its model backend from `stage_config["provider"]` at runtime — no branches in pipeline code. Swap mlx-whisper for faster-whisper by editing config only.

**Architecture:** Three abstract base classes (`WhisperProvider`, `LLMProvider`, `DiarizeProvider`) in `pipeline/providers/base.py`. Concrete implementations in `whisper.py`, `llm.py`, `diarize.py`. Factory functions in `pipeline/providers/__init__.py` instantiate the right class from config. `pipeline/stages.py` receives provider instances injected by the caller (runner/worker) — no direct service URL calls inside stages.

**Tech Stack:** Python ABCs, httpx (async-compatible but called in thread), ollama SDK, existing diarize service client

## Global Constraints

- Provider classes are **instantiated once per job** by the runner/worker; not singletons
- All provider methods are **synchronous blocking** — stages run in a thread pool (same as today)
- Provider constructors accept only the `config` dict from stage_plan — no global config dependency
- Adding a new provider = add a subclass + register in factory; no other file changes
- Tests use mock providers — never hit real services
- Commit after every task

---

## File Structure

```
# Create
pipeline/providers/
  __init__.py    — factory functions: get_whisper_provider(), get_llm_provider(), get_diarize_provider()
  base.py        — WhisperProvider ABC, LLMProvider ABC, DiarizeProvider ABC
  whisper.py     — MlxWhisperProvider, FasterWhisperProvider, OpenAIWhisperProvider
  llm.py         — OllamaLLMProvider, OpenAILLMProvider
  diarize.py     — SpeechbrainDiarizeProvider, PyannoteDiarizeProvider

# Modify
pipeline/stages.py   — accept provider instances as parameters; remove direct httpx/ollama calls
pipeline/runner.py   — build provider instances from stage config; inject into stage adapters

# Test
tests/test_providers.py   — factory and interface tests (all mocked)
tests/test_stages_providers.py  — stages receive mock providers and call correct methods
```

---

## Interfaces

**Produces** (consumed by Sub-plan D Progress Worker):

```python
# pipeline/providers/base.py
class WhisperProvider:
    def transcribe_segments(self, audio_path: Path, language: str) -> list[dict]: ...
    # Returns: [{"id", "start", "end", "text", "avg_logprob", "no_speech_prob"}]

class LLMProvider:
    def chat(self, prompt: str) -> str: ...

class DiarizeProvider:
    def diarize(self, audio_path: Path, segments: list[dict], num_speakers: int | None) -> list[dict]: ...
    # Returns: [{"speaker": "SPEAKER_00", "start": 1.0, "end": 3.5}]

# pipeline/providers/__init__.py
def get_whisper_provider(config: dict) -> WhisperProvider: ...
def get_llm_provider(config: dict) -> LLMProvider: ...
def get_diarize_provider(config: dict) -> DiarizeProvider: ...

# pipeline/stages.py — updated signatures
def transcribe(audio_path: Path, stem: str, output_dir: Path,
               cfg: dict, whisper: WhisperProvider) -> Path: ...
def summarize(srt_path: Path, stem: str, output_dir: Path,
              cfg: dict, llm: LLMProvider) -> tuple[Path, Path]: ...
def diarize(audio_path: Path, srt_path: Path, stem: str,
            output_dir: Path, cfg: dict,
            diarize_provider: DiarizeProvider) -> Path: ...
# preprocess, verify_segments, correct_srt, detect_chapters: similar pattern
```

---

## Task 1: Abstract Base Classes

**Files:**
- Create: `pipeline/providers/base.py`
- Create: `pipeline/providers/__init__.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_providers.py
import pytest
from pathlib import Path
from unittest.mock import MagicMock

def test_whisper_provider_is_abstract():
    from pipeline.providers.base import WhisperProvider
    with pytest.raises(TypeError):
        WhisperProvider()  # cannot instantiate abstract class

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
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
source venv/bin/activate
pytest tests/test_providers.py -v
```

Expected: `ModuleNotFoundError: No module named 'pipeline.providers'`

- [ ] **Step 3: Write pipeline/providers/base.py**

```python
# pipeline/providers/base.py
from abc import ABC, abstractmethod
from pathlib import Path


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
        num_speakers: int | None,
    ) -> list[dict]:
        """Return [{speaker, start, end}] aligned to input segments."""
```

- [ ] **Step 4: Write empty provider modules so imports resolve**

```python
# pipeline/providers/whisper.py
from .base import WhisperProvider

class MlxWhisperProvider(WhisperProvider):
    def __init__(self, config: dict):
        self.service_url = config.get("service_url", "http://localhost:9001")
        self.language = config.get("language", "zh")
        self.model = config.get("model", "medium")

    def transcribe_segments(self, audio_path, language):
        raise NotImplementedError("implement in Task 2")

class FasterWhisperProvider(WhisperProvider):
    def __init__(self, config: dict):
        self.service_url = config.get("service_url", "http://localhost:9001")
        self.language = config.get("language", "zh")
        self.model = config.get("model", "medium")

    def transcribe_segments(self, audio_path, language):
        raise NotImplementedError("implement in Task 2")

class OpenAIWhisperProvider(WhisperProvider):
    def __init__(self, config: dict):
        import os
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = config.get("model", "whisper-1")
        self.language = config.get("language", "zh")

    def transcribe_segments(self, audio_path, language):
        raise NotImplementedError("implement in Task 2")
```

```python
# pipeline/providers/llm.py
from .base import LLMProvider

class OllamaLLMProvider(LLMProvider):
    def __init__(self, config: dict):
        self.model = config.get("model", "qwen2.5:7b")
        self.service_url = config.get("service_url", "http://localhost:11434")

    def chat(self, prompt: str) -> str:
        raise NotImplementedError("implement in Task 3")

class OpenAILLMProvider(LLMProvider):
    def __init__(self, config: dict):
        import os
        self.api_key = os.getenv("OPENAI_API_KEY", "")
        self.model = config.get("model", "gpt-4o-mini")

    def chat(self, prompt: str) -> str:
        raise NotImplementedError("implement in Task 3")
```

```python
# pipeline/providers/diarize.py
from .base import DiarizeProvider

class SpeechbrainDiarizeProvider(DiarizeProvider):
    def __init__(self, config: dict):
        self.service_url = config.get("service_url", "http://localhost:9003")
        self.speaker_format = config.get("speaker_format", "【{label}】")

    def diarize(self, audio_path, segments, num_speakers):
        raise NotImplementedError("implement in Task 4")

class PyannoteDiarizeProvider(DiarizeProvider):
    def __init__(self, config: dict):
        import os
        self.hf_token = os.getenv("HF_TOKEN", "")
        self.speaker_format = config.get("speaker_format", "【{label}】")

    def diarize(self, audio_path, segments, num_speakers):
        raise NotImplementedError("implement in Task 4")
```

```python
# pipeline/providers/__init__.py
from .base import WhisperProvider, LLMProvider, DiarizeProvider
from .whisper import MlxWhisperProvider, FasterWhisperProvider, OpenAIWhisperProvider
from .llm import OllamaLLMProvider, OpenAILLMProvider
from .diarize import SpeechbrainDiarizeProvider, PyannoteDiarizeProvider

__all__ = [
    "WhisperProvider", "LLMProvider", "DiarizeProvider",
    "get_whisper_provider", "get_llm_provider", "get_diarize_provider",
]

_WHISPER_PROVIDERS = {
    "mlx-whisper":     MlxWhisperProvider,
    "faster-whisper":  FasterWhisperProvider,
    "openai":          OpenAIWhisperProvider,
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
        raise ValueError(f"Unknown whisper provider: {key!r}. "
                         f"Valid: {list(_WHISPER_PROVIDERS)}")
    return cls(config)


def get_llm_provider(config: dict) -> LLMProvider:
    key = config.get("provider", "ollama")
    cls = _LLM_PROVIDERS.get(key)
    if not cls:
        raise ValueError(f"Unknown LLM provider: {key!r}. "
                         f"Valid: {list(_LLM_PROVIDERS)}")
    return cls(config)


def get_diarize_provider(config: dict) -> DiarizeProvider:
    key = config.get("provider", "speechbrain")
    cls = _DIARIZE_PROVIDERS.get(key)
    if not cls:
        raise ValueError(f"Unknown diarize provider: {key!r}. "
                         f"Valid: {list(_DIARIZE_PROVIDERS)}")
    return cls(config)
```

- [ ] **Step 5: Run tests — all 7 should pass**

```bash
pytest tests/test_providers.py -v
```

Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add pipeline/providers/
git commit -m "feat(providers): base ABCs + factory functions for whisper/llm/diarize"
```

---

## Task 2: Implement MlxWhisperProvider

**Files:**
- Modify: `pipeline/providers/whisper.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_providers.py — add these tests
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

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
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_providers.py::test_mlx_whisper_transcribe_segments_calls_service -v
```

Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Implement MlxWhisperProvider.transcribe_segments**

```python
# pipeline/providers/whisper.py — replace MlxWhisperProvider body
import httpx
from pathlib import Path

class MlxWhisperProvider(WhisperProvider):
    def __init__(self, config: dict):
        self.service_url = config.get("service_url", "http://localhost:9001")
        self.language = config.get("language", "zh")
        self.model = config.get("model", "medium")
        self.initial_prompt = config.get("initial_prompt", "")
        self.timeout = float(config.get("timeout_sec", 1800))

    def transcribe_segments(self, audio_path: Path, language: str) -> list[dict]:
        with open(audio_path, "rb") as f:
            params = {"language": language or self.language}
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
```

Implement `FasterWhisperProvider` identically (same HTTP API, same endpoint).

For `OpenAIWhisperProvider`:

```python
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
        # Normalise to mediaflow segment format
        return [
            {
                "id": i,
                "start": s.start,
                "end": s.end,
                "text": s.text,
                "avg_logprob": -0.3,    # not provided by OpenAI
                "no_speech_prob": 0.0,
            }
            for i, s in enumerate(transcript.segments or [])
        ]
```

- [ ] **Step 4: Run tests**

```bash
pytest tests/test_providers.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/providers/whisper.py
git commit -m "feat(providers): implement MlxWhisperProvider + FasterWhisperProvider + OpenAIWhisperProvider"
```

---

## Task 3: Implement OllamaLLMProvider + OpenAILLMProvider

**Files:**
- Modify: `pipeline/providers/llm.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_providers.py — add
def test_ollama_llm_calls_chat(monkeypatch):
    from pipeline.providers.llm import OllamaLLMProvider
    mock_resp = {"message": {"content": "這是摘要"}}
    import ollama as _ollama
    monkeypatch.setattr(_ollama, "chat", lambda **kw: mock_resp)
    p = OllamaLLMProvider({"model": "qwen2.5:7b"})
    result = p.chat("summarize this")
    assert result == "這是摘要"

def test_openai_llm_calls_completions(monkeypatch):
    from pipeline.providers.llm import OpenAILLMProvider
    import openai
    fake_completion = MagicMock()
    fake_completion.choices[0].message.content = "summary text"
    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_completion
    monkeypatch.setattr(openai, "OpenAI", lambda **kw: fake_client)
    p = OpenAILLMProvider({"model": "gpt-4o-mini"})
    result = p.chat("summarize this")
    assert result == "summary text"
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_providers.py::test_ollama_llm_calls_chat -v
```

Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Implement OllamaLLMProvider**

```python
# pipeline/providers/llm.py
import ollama as _ollama
from .base import LLMProvider

class OllamaLLMProvider(LLMProvider):
    def __init__(self, config: dict):
        self.model = config.get("model", "qwen2.5:7b")
        host = config.get("service_url", "http://localhost:11434")
        self._client = _ollama.Client(host=host)

    def chat(self, prompt: str) -> str:
        resp = self._client.chat(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp["message"]["content"]


class OpenAILLMProvider(LLMProvider):
    def __init__(self, config: dict):
        import os, openai
        self._client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
        self.model = config.get("model", "gpt-4o-mini")

    def chat(self, prompt: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content
```

- [ ] **Step 4: Run all provider tests**

```bash
pytest tests/test_providers.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/providers/llm.py
git commit -m "feat(providers): implement OllamaLLMProvider + OpenAILLMProvider"
```

---

## Task 4: Implement SpeechbrainDiarizeProvider

**Files:**
- Modify: `pipeline/providers/diarize.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_providers.py — add
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
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_providers.py::test_speechbrain_diarize_calls_service -v
```

Expected: FAIL — `NotImplementedError`

- [ ] **Step 3: Implement SpeechbrainDiarizeProvider**

```python
# pipeline/providers/diarize.py
import json
import httpx
from pathlib import Path
from .base import DiarizeProvider


class SpeechbrainDiarizeProvider(DiarizeProvider):
    def __init__(self, config: dict):
        self.service_url = config.get("service_url", "http://localhost:9003")
        self.speaker_format = config.get("speaker_format", "【{label}】")
        self.timeout = float(config.get("timeout_sec", 600))

    def diarize(
        self, audio_path: Path, segments: list[dict], num_speakers: int | None
    ) -> list[dict]:
        with open(audio_path, "rb") as f:
            data = {"segments": json.dumps([
                {"start": s["start"], "end": s["end"]} for s in segments
            ])}
            if num_speakers:
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

    def _load(self):
        if self._pipeline is None:
            from pyannote.audio import Pipeline
            self._pipeline = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-3.1",
                use_auth_token=self.hf_token,
            )

    def diarize(
        self, audio_path: Path, segments: list[dict], num_speakers: int | None
    ) -> list[dict]:
        self._load()
        kwargs = {}
        if num_speakers:
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
```

- [ ] **Step 4: Run all provider tests**

```bash
pytest tests/test_providers.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/providers/diarize.py
git commit -m "feat(providers): implement SpeechbrainDiarizeProvider + PyannoteDiarizeProvider"
```

---

## Task 5: Update pipeline/stages.py to Accept Provider Instances

**Files:**
- Modify: `pipeline/stages.py`
- Create: `tests/test_stages_providers.py`

- [ ] **Step 1: Write failing tests for new stage signatures**

```python
# tests/test_stages_providers.py
import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

@pytest.fixture
def tmp_workspace(tmp_path):
    ws = tmp_path / "workspace"
    (ws / "2_processing").mkdir(parents=True)
    (ws / "3_output").mkdir(parents=True)
    return ws

@pytest.fixture
def fake_audio(tmp_workspace):
    p = tmp_workspace / "2_processing" / "test_clean.wav"
    p.write_bytes(b"RIFF fake wav")
    return p

def test_transcribe_uses_whisper_provider(fake_audio, tmp_workspace):
    from pipeline.stages import transcribe
    mock_whisper = MagicMock()
    mock_whisper.transcribe_segments.return_value = [
        {"id": 0, "start": 0.0, "end": 2.0, "text": "你好",
         "avg_logprob": -0.3, "no_speech_prob": 0.1}
    ]
    srt_path = transcribe(
        audio_path=fake_audio,
        stem="test",
        output_dir=tmp_workspace / "3_output",
        cfg={"language": "zh"},
        whisper=mock_whisper,
    )
    assert mock_whisper.transcribe_segments.called
    assert srt_path.exists()
    assert srt_path.suffix == ".srt"

def test_summarize_uses_llm_provider(tmp_workspace, tmp_path):
    from pipeline.stages import summarize
    srt = tmp_workspace / "3_output" / "test.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:02,000\n你好\n\n")
    mock_llm = MagicMock()
    mock_llm.chat.return_value = '{"overview":"ok","key_moments":[],"topic_segments":[]}'
    md_path, json_path = summarize(
        srt_path=srt,
        stem="test",
        output_dir=tmp_workspace / "3_output",
        cfg={"prompt_key": "summarize", "recording_type": "general"},
        llm=mock_llm,
    )
    assert mock_llm.chat.called
    assert md_path.exists()
    assert json_path.exists()
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_stages_providers.py -v
```

Expected: FAIL — `TypeError: transcribe() got an unexpected keyword argument 'whisper'`

- [ ] **Step 3: Update transcribe() in pipeline/stages.py**

Find the `def transcribe(...)` function. Add `whisper: "WhisperProvider | None" = None` parameter. Replace the direct `httpx.post(...)` call with:

```python
def transcribe(audio_path: Path, stem: str, output_dir: Path,
               cfg: dict, whisper=None) -> Path:
    if whisper is None:
        # backwards-compat: build from cfg (used by legacy runner)
        from pipeline.providers import get_whisper_provider
        whisper = get_whisper_provider({
            "provider": "mlx-whisper",
            "service_url": cfg.get("whisper", {}).get("service_url", "http://localhost:9001"),
            "language": cfg.get("whisper", {}).get("language", "zh"),
            "model": "medium",
        })
    language = cfg.get("whisper", {}).get("language", "zh")
    segments = whisper.transcribe_segments(audio_path, language)
    # ... rest of existing segment → SRT conversion logic (unchanged)
```

Similarly update `summarize()` and `diarize()` to accept optional `llm=None` and `diarize_provider=None` parameters with the same fallback pattern.

- [ ] **Step 4: Run all tests**

```bash
pytest tests/test_stages_providers.py tests/test_stages.py -v
```

Expected: all PASS. (Existing `test_stages.py` continues to work via fallback.)

- [ ] **Step 5: Commit**

```bash
git add pipeline/stages.py tests/test_stages_providers.py
git commit -m "feat(stages): accept provider instances; fallback to legacy config for compat"
```

---

## Task 6: Update pipeline/runner.py to Build + Inject Providers

**Files:**
- Modify: `pipeline/runner.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_runner_providers.py
import pytest
from unittest.mock import MagicMock, patch

def test_runner_builds_whisper_provider_from_stage_config():
    """runner.execute() injects provider instances built from stage config."""
    from pipeline.runner import _build_providers_for_stage
    from pipeline.providers.whisper import MlxWhisperProvider
    stage_cfg = {"stage": "transcribe",
                 "config": {"provider": "mlx-whisper", "language": "zh", "model": "medium"}}
    providers = _build_providers_for_stage(stage_cfg)
    assert isinstance(providers["whisper"], MlxWhisperProvider)

def test_runner_builds_llm_provider_for_summarize():
    from pipeline.runner import _build_providers_for_stage
    from pipeline.providers.llm import OllamaLLMProvider
    stage_cfg = {"stage": "summarize",
                 "config": {"provider": "ollama", "model": "qwen2.5:7b"}}
    providers = _build_providers_for_stage(stage_cfg)
    assert isinstance(providers["llm"], OllamaLLMProvider)
```

- [ ] **Step 2: Run to confirm failure**

```bash
pytest tests/test_runner_providers.py -v
```

Expected: FAIL — `ImportError: cannot import name '_build_providers_for_stage'`

- [ ] **Step 3: Add _build_providers_for_stage to pipeline/runner.py**

Add this function at the top of runner.py (after imports):

```python
from pipeline.providers import get_whisper_provider, get_llm_provider, get_diarize_provider

_WHISPER_STAGES  = {"transcribe", "verify_segments"}
_LLM_STAGES      = {"correct_srt", "summarize", "detect_chapters"}
_DIARIZE_STAGES  = {"diarize"}


def _build_providers_for_stage(stage_def: dict) -> dict:
    """Build provider instances for a stage definition from dag_flows.stage_plan."""
    stage_id = stage_def["stage"]
    cfg = stage_def.get("config", {})
    providers = {}
    if stage_id in _WHISPER_STAGES:
        providers["whisper"] = get_whisper_provider(cfg)
    if stage_id in _LLM_STAGES:
        providers["llm"] = get_llm_provider(cfg)
    if stage_id in _DIARIZE_STAGES:
        providers["diarize_provider"] = get_diarize_provider(cfg)
    return providers
```

Then update the stage adapter calls in `execute()` to unpack and pass providers.

- [ ] **Step 4: Run all tests**

```bash
pytest tests/test_runner_providers.py tests/test_runner_stop_after.py -v
```

Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add pipeline/runner.py tests/test_runner_providers.py
git commit -m "feat(runner): build and inject provider instances from stage config"
```

---

## Self-Review Checklist

- [ ] `get_whisper_provider({"provider": "unknown"})` raises `ValueError`
- [ ] `MlxWhisperProvider` and `FasterWhisperProvider` use the same HTTP endpoint — no code duplication
- [ ] `transcribe()` in `stages.py` still works with old-style `cfg` dict (no `whisper` arg) — legacy runner unbroken
- [ ] `OllamaLLMProvider` uses `ollama.Client(host=...)` not the module-level `ollama.chat()` — respects `service_url`
- [ ] `PyannoteDiarizeProvider._load()` is lazy — model not loaded until `diarize()` is called
- [ ] No provider class imports from another provider class — zero cross-dependencies
- [ ] All provider tests use mocks — no real network calls

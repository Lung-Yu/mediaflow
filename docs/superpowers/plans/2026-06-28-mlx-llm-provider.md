# MLX LLM Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Ollama HTTP dependency with a lazy-loading MLX-native LLM provider so the 8 GB model is only in memory during LLM stages, freeing RAM for Whisper and reducing peak swap usage.

**Architecture:** Introduce a thin `LLMProvider` abstraction with two backends — `MLXLLMProvider` (default, uses `mlx-lm` directly) and `OllamaLLMProvider` (backward-compat fallback). The worker calls `provider.unload()` after each job, clearing the model from Metal memory. All four Ollama call-sites in `stages.py` are replaced with a single `_llm_chat(provider, prompt)` helper that takes the provider instance instead of a model string.

**Tech Stack:** `mlx-lm` (Apple Silicon, `mlx-community` HuggingFace models), existing `ollama` Python SDK as fallback, `threading.Lock` for safe lazy-init

## Global Constraints

- Host-only (Apple Silicon). All MLX code runs in the worker process, never in the Docker API container.
- `pipeline/providers/` is a new package. Do not add providers to `pipeline/stages.py` or `pipeline/worker.py` — only import from `pipeline.providers.llm`.
- Config key changes: `ollama.model` → `llm.model`, `ollama.service_url` → `llm.service_url`. Keep `ollama:` section in `config.yaml.example` as a commented-out reference.
- Default backend: `mlx`. Default model: `mlx-community/Qwen2.5-14B-Instruct-4bit`.
- `requirements-worker.txt` is the only place to add `mlx-lm` (not `requirements.txt` — Docker can't build it).
- Ollama is no longer a required service for the pipeline. Make its health check in `ctl.sh` and Grafana optional (same treatment as diarize).
- Four call-sites in `stages.py` use Ollama: `correct_srt`, `polish_srt`, `summarize`, `detect_chapters`. All must be updated.
- The worker's `_mem_unload_ollama` in `worker.py` must be replaced with `provider.unload()`.

---

### Task 1: LLM provider abstraction + MLX implementation

**Files:**
- Create: `pipeline/providers/__init__.py`
- Create: `pipeline/providers/llm.py` — abstract base class + `OllamaLLMProvider` + factory `get_llm_provider(cfg)`
- Create: `pipeline/providers/llm_mlx.py` — `MLXLLMProvider` with lazy load/unload
- Create: `tests/test_llm_provider.py`

**Interfaces:**
- Produces: `LLMProvider.chat(prompt: str) -> str`, `LLMProvider.unload() -> None`
- Produces: `get_llm_provider(cfg: dict) -> LLMProvider` — factory, keyed on `cfg["llm"]["backend"]`

- [ ] **Step 1: Create package init**

```python
# pipeline/providers/__init__.py
```
(empty — just marks the package)

- [ ] **Step 2: Write failing tests**

```python
# tests/test_llm_provider.py
import pytest
from unittest.mock import MagicMock, patch
from pipeline.providers.llm import get_llm_provider, OllamaLLMProvider, LLMProvider

def _cfg(backend="ollama", model="qwen2.5:14b", url="http://localhost:11434"):
    return {"llm": {"backend": backend, "model": model, "service_url": url}}

def test_factory_returns_ollama_for_ollama_backend():
    p = get_llm_provider(_cfg(backend="ollama"))
    assert isinstance(p, OllamaLLMProvider)

def test_factory_returns_mlx_for_mlx_backend():
    from pipeline.providers.llm import MLXLLMProvider
    p = get_llm_provider(_cfg(backend="mlx", model="mlx-community/Qwen2.5-14B-Instruct-4bit"))
    assert isinstance(p, MLXLLMProvider)

def test_ollama_provider_chat():
    mock_resp = MagicMock()
    mock_resp.__getitem__ = lambda self, k: {"message": {"content": "hello"}}[k]
    with patch("ollama.chat", return_value=mock_resp):
        p = OllamaLLMProvider(model="qwen2.5:14b", service_url="http://localhost:11434")
        result = p.chat("hi")
    assert result == "hello"

def test_ollama_provider_unload_is_safe():
    p = OllamaLLMProvider(model="qwen2.5:14b", service_url="http://localhost:11434")
    p.unload()  # must not raise

def test_mlx_provider_unload_before_load_is_safe():
    from pipeline.providers.llm_mlx import MLXLLMProvider
    p = MLXLLMProvider(model_id="mlx-community/Qwen2.5-14B-Instruct-4bit")
    p.unload()  # must not raise even if never loaded
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
source venv/bin/activate && pytest tests/test_llm_provider.py -v
```
Expected: ImportError or ModuleNotFoundError (module doesn't exist yet)

- [ ] **Step 4: Write `pipeline/providers/llm.py`**

```python
from __future__ import annotations
import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


class LLMProvider(ABC):
    @abstractmethod
    def chat(self, prompt: str) -> str: ...

    @abstractmethod
    def unload(self) -> None: ...


class OllamaLLMProvider(LLMProvider):
    def __init__(self, model: str, service_url: str):
        self._model = model
        self._url = service_url

    def chat(self, prompt: str) -> str:
        import ollama as _ollama
        try:
            resp = _ollama.chat(
                model=self._model,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp["message"]["content"].strip()
        except Exception as exc:
            log.warning("Ollama unavailable: %s", exc)
            return ""

    def unload(self) -> None:
        import httpx
        try:
            httpx.post(
                f"{self._url}/api/generate",
                json={"model": self._model, "keep_alive": 0},
                timeout=10,
            )
            log.info("Ollama model unloaded")
        except Exception as exc:
            log.warning("Ollama unload failed: %s", exc)


def get_llm_provider(cfg: dict) -> LLMProvider:
    from pipeline.providers.llm_mlx import MLXLLMProvider
    llm_cfg = cfg.get("llm", {})
    backend = llm_cfg.get("backend", "mlx")
    model = llm_cfg.get("model", "mlx-community/Qwen2.5-14B-Instruct-4bit")
    if backend == "ollama":
        url = llm_cfg.get("service_url", "http://localhost:11434")
        return OllamaLLMProvider(model=model, service_url=url)
    return MLXLLMProvider(model_id=model)
```

- [ ] **Step 5: Write `pipeline/providers/llm_mlx.py`**

```python
from __future__ import annotations
import logging
import threading

from pipeline.providers.llm import LLMProvider

log = logging.getLogger(__name__)


class MLXLLMProvider(LLMProvider):
    def __init__(self, model_id: str):
        self._model_id = model_id
        self._model = None
        self._tokenizer = None
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        log.info("MLX LLM: loading %s", self._model_id)
        from mlx_lm import load
        self._model, self._tokenizer = load(self._model_id)
        log.info("MLX LLM: loaded")

    def chat(self, prompt: str) -> str:
        try:
            with self._lock:
                self._ensure_loaded()
            from mlx_lm import generate
            from mlx_lm.utils import load_config
            # Build chat-format prompt using tokenizer's template
            messages = [{"role": "user", "content": prompt}]
            formatted = self._tokenizer.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True
            )
            output = generate(
                self._model, self._tokenizer,
                prompt=formatted, max_tokens=4096, verbose=False,
            )
            return output.strip()
        except Exception as exc:
            log.warning("MLX LLM error: %s", exc)
            return ""

    def unload(self) -> None:
        with self._lock:
            if self._model is None:
                return
            import mlx.core as mx
            self._model = None
            self._tokenizer = None
            mx.metal.clear_cache()
            log.info("MLX LLM: unloaded, Metal cache cleared")
```

- [ ] **Step 6: Run tests**

```bash
source venv/bin/activate && pytest tests/test_llm_provider.py -v
```
Expected: all 5 tests PASS

- [ ] **Step 7: Commit**

```bash
git add pipeline/providers/__init__.py pipeline/providers/llm.py pipeline/providers/llm_mlx.py tests/test_llm_provider.py
git commit -m "feat(providers): add LLMProvider abstraction with MLX and Ollama backends"
```

---

### Task 2: Wire `stages.py` to use provider

**Files:**
- Modify: `pipeline/stages.py` — replace `import ollama`, `_ollama_chat`, and all four call-sites
- Modify: `config.yaml.example` — add `llm:` section, comment out `ollama:`
- Modify: `config.yaml` — same changes (local, not committed)

**Interfaces:**
- Consumes: `LLMProvider` from Task 1 (`pipeline.providers.llm`)
- Produces: all four stage functions now accept an optional `provider: LLMProvider | None = None` param; if None, they call `get_llm_provider(cfg)` internally (backward-compat for rerun.py)

- [ ] **Step 1: Write failing test for wiring**

Add to `tests/test_llm_provider.py`:
```python
def test_stages_use_provider_not_ollama_directly():
    """stages.py must not import ollama at module level after this task."""
    import importlib, ast, pathlib
    src = pathlib.Path("pipeline/stages.py").read_text()
    tree = ast.parse(src)
    top_imports = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        and getattr(n, "col_offset", 0) == 0
    ]
    names = [
        alias.name for node in top_imports
        for alias in getattr(node, "names", [])
    ]
    modules = [getattr(n, "module", "") or "" for n in top_imports]
    assert "ollama" not in names, "ollama must not be a top-level import in stages.py"
    assert not any("ollama" in m for m in modules)
```

Run: `pytest tests/test_llm_provider.py::test_stages_use_provider_not_ollama_directly -v`
Expected: FAIL (`ollama` still imported at top of stages.py)

- [ ] **Step 2: Update `stages.py` imports**

Remove line 25:
```python
import ollama as _ollama
```

Add after the existing imports:
```python
from pipeline.providers.llm import LLMProvider, get_llm_provider
```

- [ ] **Step 3: Replace `_ollama_chat` helper**

Replace the entire `_ollama_chat` function (lines ~677-683) with:

```python
def _llm_chat(provider: LLMProvider, prompt: str) -> str:
    return provider.chat(prompt)
```

- [ ] **Step 4: Update `correct_srt` signature and call-site**

Change function signature from:
```python
def correct_srt(stem: str, srt_path: Path, cfg: dict) -> Path:
    model = cfg["ollama"].get("model", "qwen2.5:7b")
```
To:
```python
def correct_srt(stem: str, srt_path: Path, cfg: dict, provider: LLMProvider | None = None) -> Path:
    if provider is None:
        provider = get_llm_provider(cfg)
```

Replace the `_ollama_chat` call:
```python
# before:
raw = _ollama_chat(model, PROMPTS["correct_srt"]["base"] + "\n" + lines_in)
# after:
raw = _llm_chat(provider, PROMPTS["correct_srt"]["base"] + "\n" + lines_in)
```

- [ ] **Step 5: Update `polish_srt` signature and call-site**

Same pattern as Step 4:
```python
def polish_srt(stem: str, srt_path: Path, cfg: dict, provider: LLMProvider | None = None) -> Path:
    if provider is None:
        provider = get_llm_provider(cfg)
    # remove: model = cfg["ollama"].get(...)
    # replace _ollama_chat(model, ...) with _llm_chat(provider, ...)
```

- [ ] **Step 6: Update `summarize` signature and call-sites** (3 calls in function)

```python
def summarize(stem: str, srt_path: Path, output_dir: Path, cfg: dict, provider: LLMProvider | None = None) -> Path:
    if provider is None:
        provider = get_llm_provider(cfg)
    # remove: model = cfg["ollama"].get(...)
    # replace all 3x _ollama_chat(model, ...) with _llm_chat(provider, ...)
```

- [ ] **Step 7: Update `detect_chapters` signature and call-site**

```python
def detect_chapters(stem: str, srt_path: Path, output_dir: Path, cfg: dict, provider: LLMProvider | None = None) -> Path:
    if provider is None:
        provider = get_llm_provider(cfg)
    # remove: model = cfg["ollama"].get(...)
    # replace _ollama_chat(model, ...) with _llm_chat(provider, ...)
```

- [ ] **Step 8: Run tests**

```bash
source venv/bin/activate && pytest tests/test_llm_provider.py -v
```
Expected: all tests PASS including `test_stages_use_provider_not_ollama_directly`

- [ ] **Step 9: Update `config.yaml.example`**

Add new `llm:` section and comment out `ollama:`:
```yaml
# LLM backend for summarize, correct_srt, detect_chapters stages.
# backend: mlx   → mlx-lm direct (Apple Silicon, lazy-loaded, recommended)
# backend: ollama → Ollama HTTP server (requires Ollama running separately)
llm:
  backend: mlx
  model: mlx-community/Qwen2.5-14B-Instruct-4bit

# ollama:  (legacy — use llm: section above)
#   service_url: http://localhost:11434
#   model: qwen2.5:14b
```

- [ ] **Step 10: Update local `config.yaml`**

Apply same change to local `config.yaml` (not committed). Verify with:
```bash
python3 -c "import yaml; c=yaml.safe_load(open('config.yaml')); print(c['llm'])"
```
Expected: `{'backend': 'mlx', 'model': 'mlx-community/Qwen2.5-14B-Instruct-4bit'}`

- [ ] **Step 11: Commit**

```bash
git add pipeline/stages.py config.yaml.example tests/test_llm_provider.py
git commit -m "feat(stages): replace Ollama direct calls with LLMProvider abstraction"
```

---

### Task 3: Worker lazy load/unload + requirements + optional Ollama health

**Files:**
- Modify: `pipeline/worker.py` — replace `_mem_unload_ollama` with `provider.unload()`; pass provider to stages
- Modify: `pipeline/runner.py` — pass provider through stage adapters
- Modify: `requirements-worker.txt` — add `mlx-lm`
- Modify: `scripts/ctl.sh` — Ollama health check becomes optional (same as diarize)
- Modify: `monitoring/grafana/dashboards/overview.json` — Ollama probe shows warning not red when DOWN

**Interfaces:**
- Consumes: `get_llm_provider(cfg)`, `LLMProvider.unload()` from Task 1

- [ ] **Step 1: Add `mlx-lm` to worker requirements**

```bash
echo "mlx-lm" >> requirements-worker.txt
```

Verify:
```bash
grep mlx-lm requirements-worker.txt
```

Install in venv:
```bash
source venv/bin/activate && pip install -q mlx-lm
```

- [ ] **Step 2: Write failing test for worker unload**

Add to `tests/test_llm_provider.py`:
```python
def test_worker_no_longer_calls_mem_unload_ollama():
    import ast, pathlib
    src = pathlib.Path("pipeline/worker.py").read_text()
    assert "_mem_unload_ollama" not in src, \
        "worker.py must not call _mem_unload_ollama after this task"
```

Run: `pytest tests/test_llm_provider.py::test_worker_no_longer_calls_mem_unload_ollama -v`
Expected: FAIL

- [ ] **Step 3: Update `pipeline/worker.py`**

Remove the `_mem_unload_ollama` function (lines ~50-57).

At the top of the worker's `run()` function, after job config is loaded, add:
```python
from pipeline.providers.llm import get_llm_provider as _get_llm_provider
_llm_provider = _get_llm_provider(job_cfg)
```

Replace both calls to `_mem_unload_ollama(job_cfg)` (lines ~238, ~242) with:
```python
_llm_provider.unload()
```

- [ ] **Step 4: Pass provider through runner**

In `pipeline/runner.py`, the `execute()` function calls stage adapters. Each adapter that calls an LLM stage needs the provider. Update the ctx dict to carry it:

In `worker.py`, add provider to context before calling runner:
```python
ctx["llm_provider"] = _llm_provider
```

In `pipeline/runner.py`, update LLM stage adapters to pass provider:
```python
def _adapt_correct_srt(ctx: dict, cfg: dict) -> tuple[dict, dict]:
    out = stages.correct_srt(ctx["stem"], ctx["srt_path"], cfg,
                             provider=ctx.get("llm_provider"))
    return {**ctx, "srt_path": out}, {}

def _adapt_summarize(ctx: dict, cfg: dict) -> tuple[dict, dict]:
    out = stages.summarize(ctx["stem"], ctx["srt_path"], ctx["output_dir"], cfg,
                           provider=ctx.get("llm_provider"))
    return ctx, {}

def _adapt_detect_chapters(ctx: dict, cfg: dict) -> tuple[dict, dict]:
    out = stages.detect_chapters(ctx["stem"], ctx["srt_path"], ctx["output_dir"], cfg,
                                 provider=ctx.get("llm_provider"))
    return ctx, {}

def _adapt_polish_srt(ctx: dict, cfg: dict) -> tuple[dict, dict]:
    out = stages.polish_srt(ctx["stem"], ctx["srt_path"], cfg,
                            provider=ctx.get("llm_provider"))
    return {**ctx, "srt_path": out}, {}
```

- [ ] **Step 5: Update `ctl.sh` Ollama health check to optional**

In `do_status()`, change Ollama from required (red on fail) to optional (info on fail):

```bash
# before:
_http_ok http://localhost:11434/api/tags         && _ok "ollama :11434" || _fail "ollama :11434 (ollama serve)"

# after:
_http_ok http://localhost:11434/api/tags         && _ok "ollama :11434" || _info "ollama :11434 (optional — needed only for llm.backend=ollama)"
```

- [ ] **Step 6: Update Grafana dashboard — Ollama probe to warning color**

In `monitoring/grafana/dashboards/overview.json`, find the Ollama stat panel (id: 3) and change threshold/mappings so DOWN is orange (warning) not red:

```json
"mappings": [
  {"type": "value", "options": {
    "0": {"text": "DOWN", "color": "orange"},
    "1": {"text": "UP",   "color": "green"}
  }}
],
"thresholds": {"mode": "absolute", "steps": [
  {"color": "orange", "value": 0},
  {"color": "green",  "value": 1}
]}
```

- [ ] **Step 7: Run all tests**

```bash
source venv/bin/activate && pytest tests/test_llm_provider.py -v
```
Expected: all tests PASS

- [ ] **Step 8: Smoke test — restart worker and verify MLX loads**

```bash
bash scripts/ctl.sh restart worker
tail -f data/logs/worker.log
```
Submit a short test file. Expected log output when summarize stage runs:
```
INFO pipeline.providers.llm_mlx: MLX LLM: loading mlx-community/Qwen2.5-14B-Instruct-4bit
INFO pipeline.providers.llm_mlx: MLX LLM: loaded
...
INFO pipeline.providers.llm_mlx: MLX LLM: unloaded, Metal cache cleared
```
Confirm Ollama process is NOT needed for the job to complete.

- [ ] **Step 9: Commit**

```bash
git add pipeline/worker.py pipeline/runner.py requirements-worker.txt \
        scripts/ctl.sh monitoring/grafana/dashboards/overview.json
git commit -m "feat(worker): lazy-load MLX LLM per job, unload after; make Ollama optional"
```

---

## Self-Review

**Spec coverage:**
- [x] MLX provider with lazy load/unload
- [x] Ollama fallback backend
- [x] Factory via config `llm.backend`
- [x] All 4 call-sites updated (correct_srt, polish_srt, summarize, detect_chapters)
- [x] Worker unload after each job
- [x] requirements-worker.txt updated
- [x] Ollama made optional in health checks
- [x] config.yaml.example updated

**Type consistency check:**
- `LLMProvider.chat(prompt: str) -> str` — used consistently across all Tasks
- `LLMProvider.unload() -> None` — called in worker.py Task 3
- `get_llm_provider(cfg: dict) -> LLMProvider` — factory used in stages.py (fallback) and worker.py

**Placeholder scan:** None found.

**Known ceiling:** `ponytail:` — MLX model first-use load time adds ~10-30s latency to the first LLM stage on cold start. Acceptable; add a preload step only if this becomes a measured problem.

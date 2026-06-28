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

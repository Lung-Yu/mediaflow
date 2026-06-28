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

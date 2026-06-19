from __future__ import annotations

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
        # Dict access kept for consistency with pipeline/stages.py (_ollama_chat).
        # ollama >= 0.4.0 returns a ChatResponse object that supports both
        # subscript (resp["message"]["content"]) and attribute (resp.message.content)
        # access; we prefer subscript to match the module-level usage in stages.py.
        return resp["message"]["content"]


class OpenAILLMProvider(LLMProvider):
    def __init__(self, config: dict):
        import os
        import openai
        self._client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY", ""))
        self.model = config.get("model", "gpt-4o-mini")

    def chat(self, prompt: str) -> str:
        resp = self._client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content

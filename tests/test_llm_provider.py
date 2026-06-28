from unittest.mock import MagicMock, patch

from pipeline.providers.llm import get_llm_provider, OllamaLLMProvider, LLMProvider


def _cfg(backend="ollama", model="qwen2.5:14b", url="http://localhost:11434"):
    return {"llm": {"backend": backend, "model": model, "service_url": url}}


def test_factory_returns_ollama_for_ollama_backend():
    p = get_llm_provider(_cfg(backend="ollama"))
    assert isinstance(p, OllamaLLMProvider)


def test_factory_returns_mlx_for_mlx_backend():
    from pipeline.providers.llm_mlx import MLXLLMProvider
    p = get_llm_provider(_cfg(backend="mlx", model="mlx-community/Qwen2.5-14B-Instruct-4bit"))
    assert isinstance(p, MLXLLMProvider)


def test_ollama_provider_chat():
    mock_resp = {"message": {"content": "hello"}}
    with patch("ollama.chat", return_value=mock_resp):
        p = OllamaLLMProvider(model="qwen2.5:14b", service_url="http://localhost:11434")
        result = p.chat("hi")
    assert result == "hello"


def test_ollama_provider_unload_is_safe():
    p = OllamaLLMProvider(model="qwen2.5:14b", service_url="http://localhost:11434")
    p.unload()  # must not raise even if Ollama is not running


def test_mlx_provider_unload_before_load_is_safe():
    from pipeline.providers.llm_mlx import MLXLLMProvider
    p = MLXLLMProvider(model_id="mlx-community/Qwen2.5-14B-Instruct-4bit")
    p.unload()  # must not raise even if never loaded


def test_stages_use_provider_not_ollama_directly():
    import ast
    import pathlib
    src = pathlib.Path("pipeline/stages.py").read_text()
    tree = ast.parse(src)
    top_imports = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        and getattr(n, "col_offset", 0) == 0
    ]
    names = [alias.name for node in top_imports for alias in getattr(node, "names", [])]
    modules = [getattr(n, "module", "") or "" for n in top_imports]
    assert "ollama" not in names, "ollama must not be a top-level import in stages.py"
    assert not any("ollama" in m for m in modules)


def test_worker_no_longer_calls_mem_unload_ollama():
    import pathlib
    src = pathlib.Path("pipeline/worker.py").read_text()
    assert "_mem_unload_ollama" not in src, \
        "worker.py must not call _mem_unload_ollama after this task"

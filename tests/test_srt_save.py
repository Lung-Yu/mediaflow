import pytest
from pathlib import Path
from fastapi.testclient import TestClient

def test_save_srt_writes_file(tmp_path, monkeypatch):
    """PUT /files/{stem}/srt overwrites the SRT file and returns bytes written."""
    output_dir = tmp_path / "3_output"
    output_dir.mkdir()
    srt = output_dir / "lesson01.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n\n", encoding="utf-8")

    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    # Re-import routes after env patch so WORKSPACE is re-evaluated
    import importlib, api.routes.files as m
    importlib.reload(m)

    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(m.router)
    client = TestClient(app)

    new_content = "1\n00:00:01,000 --> 00:00:02,000\n已編輯\n\n"
    resp = client.put("/files/lesson01/srt", json={"content": new_content})
    assert resp.status_code == 200
    assert resp.json()["saved"] is True
    assert srt.read_text(encoding="utf-8") == new_content

def test_save_srt_404_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    import importlib, api.routes.files as m
    importlib.reload(m)
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(m.router)
    client = TestClient(app)
    resp = client.put("/files/nonexistent/srt", json={"content": "x"})
    assert resp.status_code == 404

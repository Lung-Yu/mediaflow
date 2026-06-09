"""Tests for GET /files/{stem}/summary."""
import tempfile
from pathlib import Path
from unittest.mock import patch
from fastapi import HTTPException
import pytest

import api.routes.files as files_module


def test_get_summary_returns_404_when_missing():
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(files_module, "OUTPUT_DIR", Path(tmpdir)):
            try:
                files_module.get_summary("nostem")
                assert False, "should have raised HTTPException"
            except HTTPException as e:
                assert e.status_code == 404


def test_get_summary_returns_content():
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        (tmp / "lesson01_summary.md").write_text("## 摘要\n\n內容在此。", encoding="utf-8")
        with patch.object(files_module, "OUTPUT_DIR", tmp):
            result = files_module.get_summary("lesson01")
    assert "摘要" in result
    assert "內容在此" in result

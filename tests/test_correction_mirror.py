from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from api.services.correction import rebuild_srt, _fmt_ts


def test_rebuild_srt_format():
    segs = [
        {"index": 1, "start": 0.0,  "end": 2.5, "text": "你好"},
        {"index": 2, "start": 3.0,  "end": 5.1, "text": "世界"},
    ]
    srt = rebuild_srt(segs)
    assert "1\n00:00:00,000 --> 00:00:02,500\n你好" in srt
    assert "2\n00:00:03,000 --> 00:00:05,100\n世界" in srt
    assert srt.endswith("\n")


def test_apply_correction_mirrors_local(tmp_path):
    """apply_correction writes to local disk when the file exists."""
    local_srt = tmp_path / "abc123_test.srt"
    local_srt.write_text("original", encoding="utf-8")

    pool   = MagicMock()
    pool.fetchrow = AsyncMock(return_value={"status": "completed", "verification_status": "unverified"})
    pool.execute  = AsyncMock()

    minio = MagicMock()

    import asyncio
    with patch("api.services.correction._OUTPUT_DIR", tmp_path):
        asyncio.run(
            __import__("api.services.correction", fromlist=["apply_correction"]).apply_correction(
                pool, minio, "abc123_test",
                [{"index": 1, "start": 0.0, "end": 1.0, "text": "fixed"}],
            )
        )

    assert "fixed" in local_srt.read_text(encoding="utf-8")
    minio.put_bytes.assert_called_once()

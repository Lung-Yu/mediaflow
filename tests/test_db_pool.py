import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_app_state_has_pool_after_lifespan():
    """Pool must be on app.state after lifespan startup."""
    import asyncpg
    fake_pool = MagicMock(spec=asyncpg.Pool)
    fake_pool.execute = AsyncMock()
    fake_pool.close = AsyncMock()

    mock_minio_client = MagicMock()
    mock_minio_client.ensure_buckets = MagicMock()
    mock_minio_client.set_bucket_lifecycle = MagicMock()

    with patch("asyncpg.create_pool", AsyncMock(return_value=fake_pool)), \
         patch("api.db.init", AsyncMock()), \
         patch("api.main.reconcile", AsyncMock()), \
         patch("api.minio_client.init_client", MagicMock()), \
         patch("api.minio_client.get_client", MagicMock(return_value=mock_minio_client)), \
         patch("api.cleanup.cleanup_loop", AsyncMock()), \
         patch("api.mq.consumer.run", AsyncMock()), \
         patch("api.mq.queue_consumer.run", AsyncMock()):
        from api.main import app, lifespan
        async with lifespan(app):
            assert app.state.pool is fake_pool

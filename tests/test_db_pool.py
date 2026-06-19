import pytest
from unittest.mock import AsyncMock, patch, MagicMock


@pytest.mark.asyncio
async def test_app_state_has_pool_after_lifespan():
    """Pool must be on app.state after lifespan startup."""
    import asyncpg
    fake_pool = MagicMock(spec=asyncpg.Pool)
    fake_pool.execute = AsyncMock()

    with patch("asyncpg.create_pool", AsyncMock(return_value=fake_pool)), \
         patch("api.db.init", AsyncMock()):
        from api.main import app, lifespan
        from contextlib import asynccontextmanager
        async with lifespan(app):
            assert app.state.pool is fake_pool

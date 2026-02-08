"""FastAPI application factory."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from shared.db.database import create_tables, close_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    await create_tables()
    yield
    await close_db()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="AresBot API",
        description="Trading bot management API",
        version="3.0.0",
        lifespan=lifespan,
    )

    from .routes import account, strategy, trade, user, worker

    app.include_router(account.router, prefix="/api/accounts", tags=["accounts"])
    app.include_router(strategy.router, prefix="/api/strategies", tags=["strategies"])
    app.include_router(trade.router, prefix="/api/trades", tags=["trades"])
    app.include_router(user.router, prefix="/api/user", tags=["user"])
    app.include_router(worker.router, prefix="/api/workers", tags=["workers"])

    @app.get("/health")
    async def health_check():
        return {"status": "ok"}

    @app.get("/health/redis")
    async def redis_health_check():
        """Check Redis connection health."""
        from shared.core.redis_client import get_redis_client
        try:
            redis_client = get_redis_client()
            if redis_client.ping():
                return {"status": "ok", "redis": "connected"}
            return {"status": "error", "redis": "disconnected"}
        except Exception as e:
            return {"status": "error", "redis": str(e)}

    return app

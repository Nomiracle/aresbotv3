"""Database connection and session management."""
import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlmodel import SQLModel


def build_database_url() -> str:
    """Build database URL from environment variables, handling special characters."""
    # 优先使用完整的 DATABASE_URL
    database_url = os.environ.get("DATABASE_URL", "")
    if database_url:
        return database_url

    # 否则从分开的环境变量构建
    host = os.environ.get("DB_HOST", "localhost")
    port = os.environ.get("DB_PORT", "3306")
    user = os.environ.get("DB_USER", "aresbot")
    password = os.environ.get("DB_PASSWORD", "")
    database = os.environ.get("DB_NAME", "aresbot")

    # URL 编码密码中的特殊字符
    encoded_password = quote_plus(password) if password else ""

    return f"mysql+aiomysql://{user}:{encoded_password}@{host}:{port}/{database}"


# Global engine and session maker
_engine = None
_async_session_maker = None


def init_db(
    database_url: str,
    pool_size: int = 5,
    max_overflow: int = 10,
    echo: bool = False,
) -> None:
    """Initialize database connection pool."""
    global _engine, _async_session_maker

    _engine = create_async_engine(
        database_url,
        pool_size=pool_size,
        max_overflow=max_overflow,
        echo=echo,
        pool_pre_ping=True,
    )

    _async_session_maker = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def create_tables() -> None:
    """Create all tables in the database."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db first.")

    async with _engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Get an async database session."""
    if _async_session_maker is None:
        raise RuntimeError("Database not initialized. Call init_db first.")

    async with _async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


@asynccontextmanager
async def get_session_context() -> AsyncGenerator[AsyncSession, None]:
    """Context manager for getting a database session."""
    if _async_session_maker is None:
        raise RuntimeError("Database not initialized. Call init_db first.")

    async with _async_session_maker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def close_db() -> None:
    """Close database connection pool."""
    global _engine, _async_session_maker

    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_maker = None


def init_db_sync() -> None:
    """
    Synchronous database initialization for Docker container startup.
    Creates all tables using synchronous SQLAlchemy engine.
    """
    from sqlalchemy import create_engine

    database_url = build_database_url()
    if not database_url:
        raise RuntimeError("Database configuration not set")

    # Convert async URL to sync URL if needed
    sync_url = database_url.replace("+aiomysql", "+pymysql").replace("+asyncpg", "")

    print(f"Initializing database tables...")

    engine = create_engine(sync_url, echo=True)

    # Import models to register them with SQLModel
    from . import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    engine.dispose()

    print("Database tables created successfully!")

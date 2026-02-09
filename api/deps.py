"""FastAPI dependencies for authentication and database sessions."""
from typing import AsyncGenerator, Optional

from fastapi import Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.database import get_session


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency to get database session."""
    async for session in get_session():
        yield session


async def get_current_user(
    x_forwarded_email: Optional[str] = Header(None),
    x_forwarded_user: Optional[str] = Header(None),
) -> str:
    """Get current user email from OAuth2 Proxy headers."""
    if x_forwarded_email:
        return x_forwarded_email

    if x_forwarded_user:
        return x_forwarded_user

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )

"""FastAPI dependencies for authentication and database sessions."""
import os
from hmac import compare_digest
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
    x_internal_auth: Optional[str] = Header(None),
) -> str:
    """Get current user email from trusted gateway headers."""
    expected_internal_auth = os.environ.get("API_INTERNAL_AUTH", "").strip()
    if not expected_internal_auth:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Server auth is not configured",
        )

    provided_internal_auth = (x_internal_auth or "").strip()
    if not provided_internal_auth or not compare_digest(provided_internal_auth, expected_internal_auth):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )

    email = (x_forwarded_email or x_forwarded_user or "").strip().lower()
    if email:
        return email

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
    )

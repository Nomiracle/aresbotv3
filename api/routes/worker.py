"""Worker management routes."""
from typing import List

from fastapi import APIRouter
from pydantic import BaseModel

from api.celery_client import get_active_workers

router = APIRouter()


class WorkerInfo(BaseModel):
    name: str
    hostname: str
    concurrency: int
    active_tasks: int


@router.get("", response_model=List[WorkerInfo])
async def list_workers():
    """Get list of active Celery workers."""
    workers = get_active_workers()
    return [WorkerInfo(**w) for w in workers]

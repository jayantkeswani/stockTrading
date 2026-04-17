"""Background task status endpoint."""

from fastapi import APIRouter

from app.core.task_registry import task_registry

router = APIRouter()


@router.get("")
async def list_tasks():
    """Return all registered background tasks with their current status."""
    return {"tasks": task_registry.get_all()}

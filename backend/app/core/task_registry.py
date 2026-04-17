"""Centralized registry for tracking background tasks and scheduled jobs."""

from __future__ import annotations

import asyncio
from datetime import datetime
from enum import StrEnum
from typing import Any

from app.core.utils import now_ist


class TaskType(StrEnum):
    SCHEDULER = "scheduler"  # APScheduler periodic jobs
    STARTUP = "startup"      # One-shot startup tasks (asyncio.create_task)
    SERVICE = "service"      # Long-running services (data feed, agent)


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    STOPPED = "stopped"


class _TaskEntry:
    __slots__ = ("name", "task_type", "status", "started_at", "completed_at", "error", "metadata")

    def __init__(
        self,
        name: str,
        task_type: TaskType,
        status: TaskStatus = TaskStatus.PENDING,
        metadata: dict[str, Any] | None = None,
    ):
        self.name = name
        self.task_type = task_type
        self.status = status
        self.started_at: datetime | None = None
        self.completed_at: datetime | None = None
        self.error: str | None = None
        self.metadata: dict[str, Any] = metadata or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.task_type,
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "error": self.error,
            "metadata": self.metadata,
        }


class TaskRegistry:
    """Singleton registry for all background tasks."""

    _instance: TaskRegistry | None = None

    def __new__(cls) -> TaskRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._tasks: dict[str, _TaskEntry] = {}
        return cls._instance

    # -- Registration -----------------------------------------------------------

    def register(
        self,
        name: str,
        task_type: TaskType,
        status: TaskStatus = TaskStatus.RUNNING,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a task. If already registered, updates status."""
        entry = self._tasks.get(name)
        if entry is None:
            entry = _TaskEntry(name, task_type, metadata=metadata)
            self._tasks[name] = entry
        entry.status = status
        if status == TaskStatus.RUNNING:
            entry.started_at = now_ist()
        if metadata:
            entry.metadata.update(metadata)

    def track_asyncio_task(
        self,
        name: str,
        task: asyncio.Task,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register an asyncio.Task and auto-update status on completion."""
        self.register(name, TaskType.STARTUP, TaskStatus.RUNNING, metadata)
        task.add_done_callback(lambda t: self._on_task_done(name, t))

    # -- Status updates ---------------------------------------------------------

    def update_status(
        self,
        name: str,
        status: TaskStatus,
        error: str | None = None,
    ) -> None:
        entry = self._tasks.get(name)
        if entry is None:
            return
        entry.status = status
        entry.error = error
        if status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.STOPPED):
            entry.completed_at = now_ist()

    # -- Query ------------------------------------------------------------------

    def get_all(self) -> list[dict[str, Any]]:
        return [e.to_dict() for e in self._tasks.values()]

    def get(self, name: str) -> dict[str, Any] | None:
        entry = self._tasks.get(name)
        return entry.to_dict() if entry else None

    # -- Internal ---------------------------------------------------------------

    def _on_task_done(self, name: str, task: asyncio.Task) -> None:
        exc = task.exception() if not task.cancelled() else None
        if task.cancelled():
            self.update_status(name, TaskStatus.STOPPED)
        elif exc:
            self.update_status(name, TaskStatus.FAILED, error=str(exc))
        else:
            self.update_status(name, TaskStatus.COMPLETED)


# Module-level singleton
task_registry = TaskRegistry()

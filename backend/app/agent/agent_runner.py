"""Main agent loop — runs as asyncio background task within FastAPI.

Supports multiple autonomy levels:
- MANUAL: Monitor only, no auto-execution
- SEMI: Auto-close SL, request confirmation for profits
- YOLO: Auto-execute signals, auto-close SL, auto-book profits
"""

import asyncio
import logging

from app.agent.auto_executor import auto_execute_signal
from app.agent.trade_monitor import monitor_positions
from app.config import settings
from app.core.database import async_session_factory
from app.core.enums import AgentAutonomyLevel
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

MONITOR_INTERVAL_SECONDS = 2


class AgentRunner:
    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None
        self._yolo_mode: bool = settings.yolo_mode

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def yolo_mode(self) -> bool:
        return self._yolo_mode

    @property
    def autonomy_level(self) -> AgentAutonomyLevel:
        if self._yolo_mode:
            return AgentAutonomyLevel.YOLO
        return AgentAutonomyLevel.SEMI

    def set_yolo_mode(self, enabled: bool) -> None:
        """Toggle YOLO mode at runtime."""
        self._yolo_mode = enabled
        logger.info("YOLO mode %s", "enabled" if enabled else "disabled")

    async def start(self):
        """Start the agent monitoring loop."""
        if self._running:
            logger.warning("Agent is already running")
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop())
        logger.info("Agent started (yolo_mode=%s)", self._yolo_mode)
        await ws_manager.broadcast("agent:status", {
            "running": True,
            "yolo_mode": self._yolo_mode,
        })

    async def stop(self):
        """Stop the agent."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Agent stopped")
        await ws_manager.broadcast("agent:status", {
            "running": False,
            "yolo_mode": self._yolo_mode,
        })

    async def on_new_signal(self, signal_id) -> None:
        """Called when a new signal is generated. In YOLO mode, auto-execute it.

        This method is invoked by the strategy runner after persisting a signal.
        """
        if not self._yolo_mode:
            return
        if not self._running:
            logger.debug("Agent not running, skipping auto-execute for signal %s", signal_id)
            return

        try:
            action = await auto_execute_signal(signal_id)
            if action:
                await ws_manager.broadcast("agent:action", action)
        except Exception:
            logger.exception("Error auto-executing signal %s", signal_id)

    async def _run_loop(self):
        """Main monitoring loop."""
        while self._running:
            try:
                async with async_session_factory() as session:
                    actions = await monitor_positions(
                        session, yolo_mode=self._yolo_mode
                    )
                    if actions:
                        for action in actions:
                            await ws_manager.broadcast("agent:action", action)
                    await session.commit()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Agent loop error: %s", e, exc_info=True)

            await asyncio.sleep(MONITOR_INTERVAL_SECONDS)


agent_runner = AgentRunner()

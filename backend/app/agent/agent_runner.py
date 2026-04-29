"""Main agent loop — runs as asyncio background task within FastAPI.

Supports multiple autonomy levels (from trading_config DB):
- MANUAL: Monitor only, no auto-execution
- SEMI: Auto-close SL, request confirmation for profits
- YOLO: Auto-execute signals, auto-close SL, auto-book profits
"""

import asyncio
import logging

from app.agent.auto_executor import auto_execute_signal
from app.core.utils import now_ist
from app.agent.notification import notify_signal_generated
from app.agent.trade_monitor import monitor_positions
from app.core.database import async_session_factory
from app.core.enums import AgentAutonomyLevel
from app.services.trading_config import get_trading_config
from app.websocket.manager import ws_manager

logger = logging.getLogger(__name__)

MONITOR_INTERVAL_SECONDS = 2


class AgentRunner:
    def __init__(self):
        self._running = False
        self._task: asyncio.Task | None = None
        self.started_at = None

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self):
        """Start the agent monitoring loop."""
        if self._running:
            logger.warning("Agent is already running")
            return
        self._running = True
        self.started_at = now_ist()
        self._task = asyncio.create_task(self._run_loop())
        cfg = await get_trading_config()
        logger.info("Agent started (autonomy=%s)", cfg.autonomy_level)
        await ws_manager.broadcast("agent:status", {
            "running": True,
            "yolo_mode": cfg.yolo_mode,
        })

    async def stop(self):
        """Stop the agent."""
        self._running = False
        self.started_at = None
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Agent stopped")
        cfg = await get_trading_config()
        await ws_manager.broadcast("agent:status", {
            "running": False,
            "yolo_mode": cfg.yolo_mode,
        })

    async def on_new_signal(self, signal_id) -> None:
        """Called when a new signal is generated.

        Always sends a Telegram notification. In YOLO mode also auto-executes
        (only executable signals).
        """
        cfg = await get_trading_config()

        # Always notify regardless of mode or executable status
        try:
            from app.core.database import async_session_factory
            from app.models.signal import Signal
            from sqlalchemy import select
            async with async_session_factory() as session:
                result = await session.execute(select(Signal).where(Signal.id == signal_id))
                sig = result.scalar_one_or_none()
            if sig:
                await notify_signal_generated(
                    symbol=sig.symbol,
                    signal_type=sig.signal_type,
                    strategy_name=sig.strategy_name,
                    entry=float(sig.entry_price),
                    stop_loss=float(sig.stop_loss),
                    target=float(sig.target_price) if sig.target_price else 0,
                    strike=float(sig.strike_price) if sig.strike_price else None,
                    expiry=str(sig.expiry_date) if sig.expiry_date else None,
                    confidence=float(sig.confidence) if sig.confidence else None,
                    instrument_type=sig.instrument_type or "OPTION",
                    blocked_reason=sig.blocked_reason,
                )
        except Exception:
            logger.exception("Error sending signal notification for %s", signal_id)

        if not cfg.yolo_mode or not self._running:
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
                cfg = await get_trading_config()
                async with async_session_factory() as session:
                    actions = await monitor_positions(
                        session, yolo_mode=cfg.yolo_mode
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

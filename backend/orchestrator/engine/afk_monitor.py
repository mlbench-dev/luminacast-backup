"""
AFK Monitor — Timer logic and escalation for product pin confirmation.
Tracks creator activity and triggers overlays/warnings when AFK.
"""
import asyncio
import logging
import json
from datetime import datetime, timezone
from typing import Optional, Callable, Awaitable
from dataclasses import dataclass, field
from enum import Enum


logger = logging.getLogger(__name__)


class EscalationLevel(str, Enum):
    NONE = "none"
    OVERLAY = "overlay"              # 1 AFK: overlay + chat CTA
    SUGGEST_PAUSE = "suggest_pause"  # 5 consecutive AFKs
    AUTO_IDLE = "auto_idle"          # 10+ consecutive AFKs


@dataclass
class AFKEvent:
    product_id: str
    product_name: str
    block_id: str
    triggered_at: datetime
    escalation: EscalationLevel


@dataclass
class AFKTimerConfig:
    timeout_seconds: int = 60
    block_id: str = ""
    product_id: str = ""
    product_name: str = ""
    block_type: str = ""


class AFKMonitor:
    """
    Monitors creator activity for product pin confirmation.
    Starts timers on product blocks, triggers escalation on AFK.
    """

    def __init__(
        self,
        on_afk_triggered: Optional[Callable[[AFKEvent], Awaitable[None]]] = None,
        on_timer_tick: Optional[Callable[[int, str, str], Awaitable[None]]] = None,
    ):
        """
        Args:
            on_afk_triggered: Async callback when AFK event fires
            on_timer_tick: Async callback(seconds_remaining, product_id, product_name)
        """
        self._on_afk_triggered = on_afk_triggered
        self._on_timer_tick = on_timer_tick
        self._active_timer: Optional[asyncio.Task] = None
        self._current_config: Optional[AFKTimerConfig] = None
        self._consecutive_afk_count = 0
        self._total_afk_events = 0
        self._afk_history: list[AFKEvent] = []
        self._is_cancelled = False

    def start_timer(self, config: AFKTimerConfig):
        """
        Start an AFK timer for a product block.
        Only starts for product-type blocks.
        """
        # Only product blocks get AFK timers
        if config.block_type not in ("product", "flash_sale"):
            return

        # Cancel existing timer
        self.cancel_timer()

        self._current_config = config
        self._is_cancelled = False
        self._active_timer = asyncio.ensure_future(self._run_timer(config))

    def cancel_timer(self):
        """Cancel the active timer (creator confirmed pin)."""
        self._is_cancelled = True
        if self._active_timer and not self._active_timer.done():
            self._active_timer.cancel()
            self._active_timer = None

        if self._current_config:
            # Reset consecutive count on successful confirmation
            self._consecutive_afk_count = 0
            self._current_config = None

    async def _run_timer(self, config: AFKTimerConfig):
        """Run the countdown timer."""
        try:
            remaining = config.timeout_seconds
            while remaining > 0 and not self._is_cancelled:
                if self._on_timer_tick:
                    await self._on_timer_tick(remaining, config.product_id, config.product_name)
                await asyncio.sleep(1)
                remaining -= 1

            if not self._is_cancelled:
                await self._trigger_afk(config)

        except asyncio.CancelledError:
            pass

    async def _trigger_afk(self, config: AFKTimerConfig):
        """Trigger an AFK event with escalation."""
        self._consecutive_afk_count += 1
        self._total_afk_events += 1

        escalation = self._get_escalation_level()

        event = AFKEvent(
            product_id=config.product_id,
            product_name=config.product_name,
            block_id=config.block_id,
            triggered_at=datetime.now(timezone.utc),
            escalation=escalation,
        )

        self._afk_history.append(event)

        logger.info(json.dumps({
            "service": "afk_monitor",
            "level": "warning",
            "message": f"AFK triggered: {escalation.value}",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "consecutive_count": self._consecutive_afk_count,
            "product_id": config.product_id,
        }))

        if self._on_afk_triggered:
            await self._on_afk_triggered(event)

    def _get_escalation_level(self) -> EscalationLevel:
        """Determine escalation level based on consecutive AFK count."""
        if self._consecutive_afk_count >= 10:
            return EscalationLevel.AUTO_IDLE
        elif self._consecutive_afk_count >= 5:
            return EscalationLevel.SUGGEST_PAUSE
        elif self._consecutive_afk_count >= 1:
            return EscalationLevel.OVERLAY
        return EscalationLevel.NONE

    @property
    def consecutive_afk_count(self) -> int:
        return self._consecutive_afk_count

    @property
    def total_afk_events(self) -> int:
        return self._total_afk_events

    @property
    def is_timer_active(self) -> bool:
        return self._active_timer is not None and not self._active_timer.done()

    @property
    def current_escalation(self) -> EscalationLevel:
        return self._get_escalation_level()

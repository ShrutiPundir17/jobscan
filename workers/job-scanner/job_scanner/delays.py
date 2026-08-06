from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass


@dataclass(frozen=True)
class DelayProfile:
    """Tunable human-like pause ranges (seconds)."""

    navigate_min: float = 1.4
    navigate_max: float = 3.8
    action_min: float = 0.35
    action_max: float = 1.25
    scroll_min: float = 0.5
    scroll_max: float = 1.8
    between_pages_min: float = 2.0
    between_pages_max: float = 5.5
    thinking_min: float = 0.8
    thinking_max: float = 2.4


DEFAULT_DELAYS = DelayProfile()


def _sample(lo: float, hi: float) -> float:
    if hi < lo:
        lo, hi = hi, lo
    # Slightly bias toward shorter waits while still spanning the range
    return random.triangular(lo, hi, lo + (hi - lo) * 0.35)


async def human_delay(min_seconds: float, max_seconds: float) -> float:
    """Sleep a randomized human-like duration. Returns the waited seconds."""
    waited = _sample(min_seconds, max_seconds)
    await asyncio.sleep(waited)
    return waited


async def pause_after_navigation(profile: DelayProfile = DEFAULT_DELAYS) -> float:
    return await human_delay(profile.navigate_min, profile.navigate_max)


async def pause_between_actions(profile: DelayProfile = DEFAULT_DELAYS) -> float:
    return await human_delay(profile.action_min, profile.action_max)


async def pause_after_scroll(profile: DelayProfile = DEFAULT_DELAYS) -> float:
    return await human_delay(profile.scroll_min, profile.scroll_max)


async def pause_between_pages(profile: DelayProfile = DEFAULT_DELAYS) -> float:
    return await human_delay(profile.between_pages_min, profile.between_pages_max)


async def pause_thinking(profile: DelayProfile = DEFAULT_DELAYS) -> float:
    return await human_delay(profile.thinking_min, profile.thinking_max)

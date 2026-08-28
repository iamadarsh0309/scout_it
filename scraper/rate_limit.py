from __future__ import annotations

import asyncio
import random


class RateLimiter:
    """Enforces a politeness delay before every navigation, including discovery pages —
    not just per-player fetches. Sofascore's aggressive fingerprinting needs a much wider
    window than FotMob's lighter SSR pages (see config/settings.py per-source defaults).
    """

    def __init__(self, min_delay_seconds: float, max_delay_seconds: float):
        self.min_delay_seconds = min_delay_seconds
        self.max_delay_seconds = max_delay_seconds

    async def wait(self) -> None:
        delay = random.uniform(self.min_delay_seconds, self.max_delay_seconds)
        await asyncio.sleep(delay)

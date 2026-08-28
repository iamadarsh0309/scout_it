from __future__ import annotations

import asyncio
import logging
from typing import Self

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

logger = logging.getLogger(__name__)

STEALTH_INIT_SCRIPT = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
window.chrome = { runtime: {} };
"""

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)

RECYCLE_AFTER_REQUESTS = 175
RECYCLE_AFTER_CONSECUTIVE_FAILURES = 5


class BrowserSessionManager:
    """Owns one headless Chromium Browser per process. Callers get a per-source
    BrowserContext (via `context_for`) that is reused across requests within a run —
    fresh contexts per request would shed cookies, which for a fingerprinting-sensitive
    target like Sofascore looks less like a real user, not more anonymous.
    """

    def __init__(self, concurrency_by_source: dict[str, int], stealth_by_source: dict[str, bool]):
        self._concurrency_by_source = concurrency_by_source
        self._stealth_by_source = stealth_by_source
        self._playwright = None
        self._browser: Browser | None = None
        self._contexts: dict[str, BrowserContext] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._request_counts: dict[str, int] = {}
        self._consecutive_failures: dict[str, int] = {}

    async def start(self) -> None:
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=True)

    async def stop(self) -> None:
        for ctx in self._contexts.values():
            await ctx.close()
        self._contexts.clear()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def __aenter__(self) -> Self:
        await self.start()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.stop()

    async def _new_context(self, source: str) -> BrowserContext:
        assert self._browser is not None
        ctx = await self._browser.new_context(
            user_agent=DEFAULT_USER_AGENT,
            viewport={"width": 1440, "height": 900},
            locale="en-US",
            timezone_id="Europe/London",
        )
        if self._stealth_by_source.get(source, True):
            await ctx.add_init_script(STEALTH_INIT_SCRIPT)
        self._request_counts[source] = 0
        self._consecutive_failures[source] = 0
        return ctx

    async def context_for(self, source: str) -> BrowserContext:
        if source not in self._contexts:
            self._contexts[source] = await self._new_context(source)
            self._semaphores[source] = asyncio.Semaphore(self._concurrency_by_source.get(source, 1))
        return self._contexts[source]

    def semaphore_for(self, source: str) -> asyncio.Semaphore:
        return self._semaphores[source]

    async def note_result(self, source: str, failed: bool) -> None:
        """Tracks per-source request volume/failures and recycles the context (fresh
        cookies/state) once it accumulates too much history or too many consecutive
        failures — sheds any bot-detection state the site may have built up."""
        self._request_counts[source] = self._request_counts.get(source, 0) + 1
        if failed:
            self._consecutive_failures[source] = self._consecutive_failures.get(source, 0) + 1
        else:
            self._consecutive_failures[source] = 0

        should_recycle = (
            self._request_counts[source] >= RECYCLE_AFTER_REQUESTS
            or self._consecutive_failures[source] >= RECYCLE_AFTER_CONSECUTIVE_FAILURES
        )
        if should_recycle and source in self._contexts:
            logger.info("Recycling browser context for %s", source)
            old_ctx = self._contexts.pop(source)
            await old_ctx.close()

    async def new_page(self, source: str) -> Page:
        ctx = await self.context_for(source)
        return await ctx.new_page()

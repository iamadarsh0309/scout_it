from __future__ import annotations

import datetime as dt
import logging
from collections.abc import AsyncIterator

from config.settings import CoverageConfig
from scraper.base import (
    BaseSourceAdapter,
    DiscoveredClub,
    DiscoveredCompetition,
    DiscoveredPlayer,
    DiscoveredSeason,
    RawFetchResult,
    competitions_from_coverage,
    seasons_from_coverage,
)
from scraper.retry import BlockedError, FetchError, looks_blocked, with_retry

logger = logging.getLogger(__name__)

API_ROOT = "https://api.sofascore.com/api/v1"


class SofascoreAdapter(BaseSourceAdapter):
    """Sofascore's frontend is a client-rendered SPA with no confirmed SSR JSON blob, so
    DOM-scraping it means brittle CSS selectors against a UI that churns more than an API
    response shape. Instead this adapter drives a real Playwright page to navigate directly
    to Sofascore's own JSON API endpoints (api.sofascore.com/api/v1/...) — this is still
    browser-driven (a genuine Chromium network/JS stack, not a bare httpx/requests client),
    which is what defeats the 403s plain HTTP clients get from Sofascore's edge fingerprinting.
    Concurrency=1 and a long rate-limit window (see config/settings.py) are load-bearing here.
    """

    source_name = "sofascore"

    def __init__(self, browser, raw_store, rate_limiter, coverage: CoverageConfig):
        super().__init__(browser, raw_store, rate_limiter)
        self.coverage = coverage

    async def _goto_json(self, url: str, entity_type: str, source_entity_id: str) -> RawFetchResult:
        await self.rate_limiter.wait()
        page = await self.browser.new_page(self.source_name)
        failed = False
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            status = resp.status if resp else None
            body_text = await resp.text() if resp else ""
            if looks_blocked(body_text):
                failed = True
                raise BlockedError(f"Block page detected for {url}")
            if status is not None and status >= 400:
                failed = True
                raise FetchError(f"HTTP {status} for {url}")
            import json

            payload = json.loads(body_text)
            return RawFetchResult(
                source=self.source_name,
                entity_type=entity_type,
                source_entity_id=source_entity_id,
                url=url,
                http_status=status,
                fetched_at=dt.datetime.now(dt.UTC),
                content_type="json",
                json_payloads=[payload],
            )
        except (BlockedError, FetchError):
            raise
        except Exception as exc:
            failed = True
            raise FetchError(str(exc)) from exc
        finally:
            await page.close()
            await self.browser.note_result(self.source_name, failed=failed)

    _goto_json_with_retry = with_retry(_goto_json)

    async def discover_competitions(self) -> AsyncIterator[DiscoveredCompetition]:
        for comp in competitions_from_coverage(self.coverage, self.source_name):
            yield comp

    async def discover_seasons(self, competition: DiscoveredCompetition) -> AsyncIterator[DiscoveredSeason]:
        for season in seasons_from_coverage(self.coverage, self.source_name, competition):
            yield season

    async def discover_clubs(
        self, competition: DiscoveredCompetition, season: DiscoveredSeason
    ) -> AsyncIterator[DiscoveredClub]:
        url = (
            f"{API_ROOT}/unique-tournament/{competition.source_competition_id}"
            f"/season/{season.source_season_id}/standings/total"
        )
        fetch = await self._goto_json_with_retry(url, "competition", competition.source_competition_id)
        self.raw_store.write(fetch)

        payload = (fetch.json_payloads or [{}])[0]
        rows = payload.get("standings", [{}])[0].get("rows", []) if payload.get("standings") else []
        seen: set[str] = set()
        for row in rows:
            team = row.get("team", {})
            team_id = team.get("id")
            name = team.get("name")
            if team_id is None or not name:
                continue
            team_id = str(team_id)
            if team_id in seen:
                continue
            seen.add(team_id)
            yield DiscoveredClub(
                source=self.source_name,
                source_club_id=team_id,
                name=name,
                url=f"{API_ROOT}/team/{team_id}/players",
            )

    async def discover_squad(
        self, club: DiscoveredClub, season: DiscoveredSeason
    ) -> AsyncIterator[DiscoveredPlayer]:
        fetch = await self._goto_json_with_retry(club.url, "club", club.source_club_id)
        self.raw_store.write(fetch)

        payload = (fetch.json_payloads or [{}])[0]
        seen: set[str] = set()
        for entry in payload.get("players", []):
            player = entry.get("player", {})
            player_id = player.get("id")
            name = player.get("name")
            if player_id is None or not name:
                continue
            player_id = str(player_id)
            if player_id in seen:
                continue
            seen.add(player_id)
            yield DiscoveredPlayer(
                source=self.source_name,
                source_player_id=player_id,
                name=name,
                source_club_id=club.source_club_id,
                shirt_number=player.get("jerseyNumber"),
                position=player.get("position"),
                url=f"{API_ROOT}/player/{player_id}",
            )

    async def fetch_player_profile(self, player: DiscoveredPlayer) -> RawFetchResult:
        fetch = await self._goto_json_with_retry(player.url, "player", player.source_player_id)
        self.raw_store.write(fetch)
        return fetch

    async def fetch_player_stats(self, player: DiscoveredPlayer, season: DiscoveredSeason) -> RawFetchResult:
        # Best-effort endpoint pattern inferred from Sofascore's per-season statistics shape
        # (see research notes) — verify against a live response before a full run; if this
        # 404s, the season/competition-wide statistics endpoint with a player filter is the
        # documented fallback (etl/parsers/sofascore_parser.py should note whichever is used).
        url = (
            f"{API_ROOT}/player/{player.source_player_id}"
            f"/unique-tournament/{season.source_competition_id}/season/{season.source_season_id}/statistics/overall"
        )
        fetch = await self._goto_json_with_retry(url, "player_stats", player.source_player_id)
        self.raw_store.write(fetch)
        return fetch

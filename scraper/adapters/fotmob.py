from __future__ import annotations

import datetime as dt
import logging
from collections.abc import AsyncIterator

from config.settings import CoverageConfig
from scraper.adapters._json_utils import extract_next_data, find_dicts_with_keys
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


class FotMobAdapter(BaseSourceAdapter):
    """Captures FotMob's server-rendered pages and extracts the embedded __NEXT_DATA__
    JSON, rather than calling apigw.fotmob.com directly (which is gated behind FotMob's
    client-computed `x-mas` signature — a scheme that has changed before and broken other
    scrapers). The raw HTML is stored untouched; JSON extraction happens again in
    etl/parsers/fotmob_parser.py so ETL can be rerun without re-scraping.
    """

    source_name = "fotmob"

    def __init__(self, browser, raw_store, rate_limiter, coverage: CoverageConfig):
        super().__init__(browser, raw_store, rate_limiter)
        self.coverage = coverage

    async def _goto_html(self, url: str, entity_type: str, source_entity_id: str) -> RawFetchResult:
        await self.rate_limiter.wait()
        page = await self.browser.new_page(self.source_name)
        failed = False
        try:
            resp = await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            html = await page.content()
            status = resp.status if resp else None
            if looks_blocked(html):
                failed = True
                raise BlockedError(f"Block page detected for {url}")
            if status is not None and status >= 400:
                failed = True
                raise FetchError(f"HTTP {status} for {url}")
            return RawFetchResult(
                source=self.source_name,
                entity_type=entity_type,
                source_entity_id=source_entity_id,
                url=url,
                http_status=status,
                fetched_at=dt.datetime.now(dt.UTC),
                content_type="html",
                html=html,
            )
        except (BlockedError, FetchError):
            raise
        except Exception as exc:  # navigation/timeout errors from Playwright
            failed = True
            raise FetchError(str(exc)) from exc
        finally:
            await page.close()
            await self.browser.note_result(self.source_name, failed=failed)

    _goto_html_with_retry = with_retry(_goto_html)

    async def discover_competitions(self) -> AsyncIterator[DiscoveredCompetition]:
        for comp in competitions_from_coverage(self.coverage, self.source_name):
            yield comp

    async def discover_seasons(self, competition: DiscoveredCompetition) -> AsyncIterator[DiscoveredSeason]:
        for season in seasons_from_coverage(self.coverage, self.source_name, competition):
            yield season

    async def discover_clubs(
        self, competition: DiscoveredCompetition, season: DiscoveredSeason
    ) -> AsyncIterator[DiscoveredClub]:
        url = f"https://www.fotmob.com/leagues/{competition.source_competition_id}/table/-"
        fetch = await self._goto_html_with_retry(url, "competition", competition.source_competition_id)
        self.raw_store.write(fetch)

        next_data = extract_next_data(fetch.html or "")
        if next_data is None:
            raise FetchError(f"__NEXT_DATA__ not found for league page {url} — site layout may have changed")

        seen: set[str] = set()
        for candidate in find_dicts_with_keys(next_data, {"id", "name"}):
            club_id = candidate.get("id")
            name = candidate.get("name")
            if club_id is None or not isinstance(name, str):
                continue
            club_id = str(club_id)
            # Bias toward team-shaped records: FotMob table rows carry an "id"+"name" for
            # many unrelated objects too, so require a team-like discriminator.
            if not any(k in candidate for k in ("pageUrl", "logo", "teamColors")):
                continue
            if club_id in seen:
                continue
            seen.add(club_id)
            yield DiscoveredClub(
                source=self.source_name,
                source_club_id=club_id,
                name=name,
                url=f"https://www.fotmob.com/teams/{club_id}/squad/-",
            )

    async def discover_squad(
        self, club: DiscoveredClub, season: DiscoveredSeason
    ) -> AsyncIterator[DiscoveredPlayer]:
        fetch = await self._goto_html_with_retry(club.url, "club", club.source_club_id)
        self.raw_store.write(fetch)

        next_data = extract_next_data(fetch.html or "")
        if next_data is None:
            raise FetchError(f"__NEXT_DATA__ not found for squad page {club.url}")

        seen: set[str] = set()
        for candidate in find_dicts_with_keys(next_data, {"id", "name"}):
            player_id = candidate.get("id")
            name = candidate.get("name")
            if player_id is None or not isinstance(name, str):
                continue
            if not any(k in candidate for k in ("shirtNumber", "positionId", "role")):
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
                shirt_number=candidate.get("shirtNumber"),
                position=candidate.get("role") or candidate.get("positionId"),
                url=f"https://www.fotmob.com/players/{player_id}/-",
            )

    async def fetch_player_profile(self, player: DiscoveredPlayer) -> RawFetchResult:
        fetch = await self._goto_html_with_retry(player.url, "player", player.source_player_id)
        self.raw_store.write(fetch)
        return fetch

    async def fetch_player_stats(self, player: DiscoveredPlayer, season: DiscoveredSeason) -> RawFetchResult:
        # FotMob embeds per-season stat breakdowns in the same player page's __NEXT_DATA__
        # (a season selector hydrates client-side from data already present), so this reuses
        # the profile page rather than guessing a separate stats URL.
        fetch = await self._goto_html_with_retry(player.url, "player_stats", player.source_player_id)
        self.raw_store.write(fetch)
        return fetch

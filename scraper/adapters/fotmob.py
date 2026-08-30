from __future__ import annotations

import datetime as dt
import json
import logging
from collections.abc import AsyncIterator
from urllib.parse import quote

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

    @staticmethod
    def _resolve_entry_id(stat_seasons: list[dict], season: DiscoveredSeason) -> str | None:
        """FotMob's per-season deep-stats endpoint is keyed by an `entryId` (e.g. "1-0"),
        not the season label itself. That id is only discoverable from a player's own
        `statSeasons` index, matched by season label + tournament id."""
        target_label = season.label.replace("-", "/")  # coverage.yaml "2025-2026" -> fotmob "2025/2026"
        for season_entry in stat_seasons:
            if season_entry.get("seasonName") != target_label:
                continue
            for tournament in season_entry.get("tournaments", []):
                if str(tournament.get("tournamentId")) == str(season.source_competition_id):
                    return tournament.get("entryId")
        return None

    async def discover_competitions(self) -> AsyncIterator[DiscoveredCompetition]:
        for comp in competitions_from_coverage(self.coverage, self.source_name):
            yield comp

    async def discover_seasons(self, competition: DiscoveredCompetition) -> AsyncIterator[DiscoveredSeason]:
        for season in seasons_from_coverage(self.coverage, self.source_name, competition):
            yield season

    async def discover_clubs(
        self, competition: DiscoveredCompetition, season: DiscoveredSeason
    ) -> AsyncIterator[DiscoveredClub]:
        # FotMob's "season id" is just its season label string (e.g. "2025/2026") passed as a
        # ?season= query param — there is no opaque numeric season id, confirmed by inspecting
        # a live page's __NEXT_DATA__.allAvailableSeasons. Without this param the page silently
        # falls back to the current season table, which would silently crawl the wrong season.
        season_qs = quote(season.source_season_id, safe="")
        url = f"https://www.fotmob.com/leagues/{competition.source_competition_id}/table/-?season={season_qs}"
        fetch = await self._goto_html_with_retry(url, "competition", competition.source_competition_id)
        self.raw_store.write(fetch)

        next_data = extract_next_data(fetch.html or "")
        if next_data is None:
            raise FetchError(f"__NEXT_DATA__ not found for league page {url} — site layout may have changed")

        seen: set[str] = set()
        for candidate in find_dicts_with_keys(next_data, {"id", "name", "pageUrl"}):
            page_url = candidate.get("pageUrl")
            # __NEXT_DATA__ contains many unrelated id+name+pageUrl-shaped objects (player
            # leaderboard rows also carry a "teamColors"/"logo" key, which is why those were
            # previously used as a discriminator and wrongly matched players too). Only a
            # "/teams/{id}/..." pageUrl reliably identifies an actual club row — confirmed
            # against a live Premier League table page (exactly 20 matches, no false positives).
            if not isinstance(page_url, str) or not page_url.startswith("/teams/"):
                continue
            club_id = candidate.get("id")
            name = candidate.get("name")
            if club_id is None or not isinstance(name, str):
                continue
            club_id = str(club_id)
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

        # A generic id+name+role search (as discover_clubs originally did for teams) picks up
        # unrelated player-shaped objects elsewhere on the page (e.g. a "similar players" or
        # team-of-the-week widget) — confirmed live: it pulled in players from other clubs
        # entirely. The actual roster lives at squad.squad: a list of position-group dicts
        # (title="coach"/"keepers"/"defenders"/"midfielders"/"attackers", each with "members").
        squad_containers = find_dicts_with_keys(next_data, {"squad"})
        groups = None
        for container in squad_containers:
            inner = container.get("squad")
            if isinstance(inner, dict) and isinstance(inner.get("squad"), list):
                groups = inner["squad"]
                break
        if groups is None:
            raise FetchError(f"squad.squad group list not found for {club.url} — site layout may have changed")

        seen: set[str] = set()
        for group in groups:
            if group.get("title") == "coach":
                continue  # technical staff, not a player
            for member in group.get("members", []):
                player_id = member.get("id")
                name = member.get("name")
                if player_id is None or not isinstance(name, str):
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
                    shirt_number=member.get("shirtNumber"),
                    position=member.get("positionIdsDesc") or (member.get("role") or {}).get("fallback"),
                    url=f"https://www.fotmob.com/players/{player_id}/-",
                )

    async def fetch_player_profile(self, player: DiscoveredPlayer) -> RawFetchResult:
        fetch = await self._goto_html_with_retry(player.url, "player", player.source_player_id)
        self.raw_store.write(fetch)
        return fetch

    async def fetch_player_stats(self, player: DiscoveredPlayer, season: DiscoveredSeason) -> RawFetchResult:
        # The player profile page's embedded __NEXT_DATA__ only carries the *current* season's
        # summary (mainLeague/firstSeasonStats) and a season->entryId index (statSeasons), not
        # historical per-season stat values — confirmed live, this was previously a bug where
        # this method just re-fetched the identical profile page. The real per-season, per-90,
        # percentile-ranked stats live at a separate JSON endpoint keyed by that entryId.
        profile_fetch = await self._goto_html_with_retry(player.url, "player", player.source_player_id)
        next_data = extract_next_data(profile_fetch.html or "")
        if next_data is None:
            raise FetchError(f"__NEXT_DATA__ not found for player page {player.url}")

        data = next_data.get("props", {}).get("pageProps", {}).get("data", {})
        entry_id = self._resolve_entry_id(data.get("statSeasons") or [], season)
        if entry_id is None:
            raise FetchError(
                f"No statSeasons entry for player {player.source_player_id}, "
                f"tournament {season.source_competition_id}, season {season.label} "
                f"(player may not have featured in this competition/season)"
            )

        stats_url = (
            f"https://www.fotmob.com/api/data/playerStats"
            f"?playerId={player.source_player_id}&seasonId={entry_id}"
        )
        fetch = await self._goto_json_with_retry(stats_url, "player_stats", player.source_player_id)
        self.raw_store.write(fetch)
        return fetch

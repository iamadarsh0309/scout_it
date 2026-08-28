from __future__ import annotations

import datetime as dt
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True)
class DiscoveredCompetition:
    source: str
    source_competition_id: str
    name: str
    country: str
    tier: int | None
    url: str


@dataclass(frozen=True)
class DiscoveredSeason:
    source: str
    source_competition_id: str
    source_season_id: str
    label: str
    url: str


@dataclass(frozen=True)
class DiscoveredClub:
    source: str
    source_club_id: str
    name: str
    url: str


@dataclass(frozen=True)
class DiscoveredPlayer:
    source: str
    source_player_id: str
    name: str
    source_club_id: str
    shirt_number: int | None
    position: str | None
    url: str


@dataclass(frozen=True)
class RawFetchResult:
    source: str
    entity_type: str
    source_entity_id: str
    url: str
    http_status: int | None
    fetched_at: dt.datetime
    content_type: str  # "html" | "json"
    html: str | None = None
    json_payloads: list[dict] | None = None


def competitions_from_coverage(coverage, source: str) -> list[DiscoveredCompetition]:
    """MVP discovery for competitions is config-driven, not a live site crawl: coverage.yaml
    already pins which competitions to track and their per-source IDs (crawling a source's
    full competitions index is Phase 4 / global-coverage scope, per ProjectPlan.md sec. 20).
    """
    out = []
    for comp in coverage.active_competitions():
        if source not in comp.sources:
            continue
        out.append(
            DiscoveredCompetition(
                source=source,
                source_competition_id=comp.source_competition_id(source),
                name=comp.name,
                country=comp.country,
                tier=comp.tier,
                url="",
            )
        )
    return out


def seasons_from_coverage(
    coverage, source: str, competition: DiscoveredCompetition
) -> list[DiscoveredSeason]:
    """Same rationale as competitions_from_coverage: season IDs per source are pinned in
    coverage.yaml rather than discovered by crawling a season-selector dropdown."""
    out = []
    for comp in coverage.active_competitions():
        if comp.source_competition_id(source) != competition.source_competition_id:
            continue
        for season in comp.seasons:
            if not season.active or source not in season.source_season_ids:
                continue
            out.append(
                DiscoveredSeason(
                    source=source,
                    source_competition_id=competition.source_competition_id,
                    source_season_id=season.source_season_ids[source],
                    label=season.label,
                    url="",
                )
            )
    return out


class BaseSourceAdapter(ABC):
    """One adapter per data source. Adapters only discover child entities/URLs and fetch raw
    artifacts (HTML / intercepted JSON) — they do not parse fields into typed models. That
    happens in etl/parsers/, so ETL stays rerunnable against raw artifacts with zero scraping.
    """

    source_name: str

    def __init__(self, browser, raw_store, rate_limiter):
        self.browser = browser
        self.raw_store = raw_store
        self.rate_limiter = rate_limiter

    @abstractmethod
    def discover_competitions(self) -> AsyncIterator[DiscoveredCompetition]: ...

    @abstractmethod
    def discover_seasons(
        self, competition: DiscoveredCompetition
    ) -> AsyncIterator[DiscoveredSeason]: ...

    @abstractmethod
    def discover_clubs(
        self, competition: DiscoveredCompetition, season: DiscoveredSeason
    ) -> AsyncIterator[DiscoveredClub]: ...

    @abstractmethod
    def discover_squad(
        self, club: DiscoveredClub, season: DiscoveredSeason
    ) -> AsyncIterator[DiscoveredPlayer]: ...

    @abstractmethod
    async def fetch_player_profile(self, player: DiscoveredPlayer) -> RawFetchResult: ...

    @abstractmethod
    async def fetch_player_stats(
        self, player: DiscoveredPlayer, season: DiscoveredSeason
    ) -> RawFetchResult: ...

from __future__ import annotations

from pydantic import BaseModel


class RawInfoItem(BaseModel):
    title: str
    translation_key: str
    number_value: float | None = None
    key: str | None = None
    fallback: str | int | dict | None = None
    country_code: str | None = None  # ISO3, only present on the "country" info item


class FotMobClubRaw(BaseModel):
    raw_artifact_path: str
    id: str
    name: str
    country_code: str | None  # ISO3, e.g. "ENG"
    squad_groups: list[dict]  # [{title, members: [...]}], passed through as-is


class FotMobPlayerRaw(BaseModel):
    raw_artifact_path: str
    id: str
    name: str
    birth_date_utc: str | None
    primary_position_label: str | None
    primary_position_key: str | None
    player_information: list[RawInfoItem]
    stat_seasons: list[dict]  # index only (seasonName -> tournaments/entryId), not stat values
    traits: dict | None  # {key, title, items: [{key, title, value}]}
    market_values: dict | None  # {values: [{date, value, currency, lowerBound, upperBound, source, teamId, teamName}]}


class StatItem(BaseModel):
    localized_title_id: str
    title: str
    stat_value: str
    per90: float | None = None
    percentile_rank: float | None = None
    percentile_rank_per90: float | None = None


class FotMobPlayerStatsRaw(BaseModel):
    raw_artifact_path: str
    player_id: str
    season_entry_id: str
    """FotMob's opaque per-player entryId (e.g. "1-0") -- a {season_index}-{tournament_index}
    pair scoped to that player's own statSeasons list, NOT parseable into a real tournament/
    competition id. Kept only for traceability; the normalizer must be told which internal
    competition_id/season_id it's populating from the ETL orchestration scope, not from this."""
    stats_items: list[StatItem]

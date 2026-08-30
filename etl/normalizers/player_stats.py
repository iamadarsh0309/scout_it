from __future__ import annotations

from etl.models_raw.fotmob import FotMobPlayerStatsRaw

# localizedTitleId -> PlayerStats named column
_PROMOTED_COLUMNS = {
    "goals": "goals",
    "assists": "assists",
    "minutes_played": "minutes_played",
    "matches_uppercase": "appearances",
}


def _to_number(stat_value: str) -> float | None:
    try:
        return float(stat_value)
    except (TypeError, ValueError):
        return None


def normalize_player_stats(raw: FotMobPlayerStatsRaw) -> dict:
    """Promotes known metrics to named PlayerStats columns; everything else (xG, shots,
    progressive actions, ...) goes into the `stats` JSONB catch-all keyed by FotMob's own
    localized_title_id, carrying value/per90/percentile so nothing FotMob already computed
    for us gets thrown away."""
    promoted: dict[str, int | None] = {col: None for col in _PROMOTED_COLUMNS.values()}
    stats_jsonb: dict = {}

    for item in raw.stats_items:
        number = _to_number(item.stat_value)
        if item.localized_title_id in _PROMOTED_COLUMNS:
            column = _PROMOTED_COLUMNS[item.localized_title_id]
            promoted[column] = int(number) if number is not None else None
        stats_jsonb[item.localized_title_id] = {
            "value": item.stat_value,
            "per90": item.per90,
            "percentile_rank": item.percentile_rank,
            "percentile_rank_per90": item.percentile_rank_per90,
        }

    return {**promoted, "stats": stats_jsonb}

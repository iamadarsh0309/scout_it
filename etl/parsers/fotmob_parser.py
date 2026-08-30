from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from etl.models_raw.fotmob import (
    FotMobClubRaw,
    FotMobPlayerRaw,
    FotMobPlayerStatsRaw,
    RawInfoItem,
    StatItem,
)
from scraper.adapters._json_utils import extract_next_data


def _read_meta(html_or_json_path: Path) -> dict:
    meta_path = html_or_json_path.with_suffix(html_or_json_path.suffix + ".meta.json")
    return json.loads(meta_path.read_text())


def parse_club(path: Path) -> FotMobClubRaw:
    html = path.read_text()
    club_id = path.parent.name  # data/raw/fotmob/club/{club_id}/{ts}.html
    next_data = extract_next_data(html)
    if next_data is None:
        raise ValueError(f"__NEXT_DATA__ not found in {path}")

    team_key = f"team-{club_id}"
    fallback = next_data.get("props", {}).get("pageProps", {}).get("fallback", {})
    team_data = fallback.get(team_key)
    if team_data is None:
        raise ValueError(f"fallback[{team_key!r}] not found in {path} — site layout may have changed")

    details = team_data.get("details", {})
    squad_container = team_data.get("squad", {})
    squad_groups = squad_container.get("squad", []) if isinstance(squad_container, dict) else []

    return FotMobClubRaw(
        raw_artifact_path=str(path),
        id=str(details.get("id", club_id)),
        name=details.get("name", ""),
        country_code=details.get("country"),
        squad_groups=squad_groups,
    )


def _parse_player_information(raw_items: list[dict]) -> list[RawInfoItem]:
    items = []
    for item in raw_items:
        value = item.get("value", {})
        items.append(
            RawInfoItem(
                title=item.get("title", ""),
                translation_key=item.get("translationKey", ""),
                number_value=value.get("numberValue"),
                key=value.get("key"),
                fallback=value.get("fallback"),
                country_code=item.get("countryCode"),
            )
        )
    return items


def parse_player(path: Path) -> FotMobPlayerRaw:
    html = path.read_text()
    next_data = extract_next_data(html)
    if next_data is None:
        raise ValueError(f"__NEXT_DATA__ not found in {path}")

    data = next_data.get("props", {}).get("pageProps", {}).get("data", {})
    position_desc = data.get("positionDescription") or {}
    primary_position = position_desc.get("primaryPosition") or {}

    return FotMobPlayerRaw(
        raw_artifact_path=str(path),
        id=str(data.get("id")),
        name=data.get("name", ""),
        birth_date_utc=(data.get("birthDate") or {}).get("utcTime"),
        primary_position_label=primary_position.get("label"),
        primary_position_key=primary_position.get("key"),
        player_information=_parse_player_information(data.get("playerInformation") or []),
        stat_seasons=data.get("statSeasons") or [],
        traits=data.get("traits"),
        market_values=data.get("marketValues"),
    )


def parse_player_stats(path: Path) -> FotMobPlayerStatsRaw:
    """`path` points at the JSON artifact written by the (fixed) fetch_player_stats — a
    single-element json_payloads array from the /api/data/playerStats endpoint. The endpoint
    itself doesn't echo back playerId/seasonId, so those are recovered from the .meta.json
    sidecar's stored URL and source_entity_id rather than from the payload body."""
    payloads = json.loads(path.read_text())
    payload = payloads[0] if payloads else {}
    meta = _read_meta(path)

    query = parse_qs(urlparse(meta["url"]).query)
    player_id = query.get("playerId", [meta["source_entity_id"]])[0]
    season_entry_id = query.get("seasonId", [""])[0]

    # `statsSection` is the detailed metric breakdown (xG, passing, defensive actions, ...)
    # but does NOT include minutes_played/matches_uppercase/player_started_matches/rating --
    # those headline summary metrics live only in `topStatCard`, confirmed live against a
    # real payload. Merge both, keyed by localizedTitleId; statsSection wins on overlap
    # (e.g. "goals"/"assists" appear in both with identical values).
    by_title_id: dict[str, StatItem] = {}
    for group in (payload.get("statsSection") or {}).get("items", []):
        for stat in group.get("items", []):
            item = StatItem(
                localized_title_id=stat.get("localizedTitleId", ""),
                title=stat.get("title", ""),
                stat_value=str(stat.get("statValue", "")),
                per90=stat.get("per90"),
                percentile_rank=stat.get("percentileRank"),
                percentile_rank_per90=stat.get("percentileRankPer90"),
            )
            by_title_id[item.localized_title_id] = item

    for stat in (payload.get("topStatCard") or {}).get("items", []):
        title_id = stat.get("localizedTitleId", "")
        if title_id in by_title_id:
            continue
        by_title_id[title_id] = StatItem(
            localized_title_id=title_id,
            title=stat.get("title", ""),
            stat_value=str(stat.get("statValue", "")),
            per90=stat.get("per90"),
            percentile_rank=stat.get("percentileRank"),
            percentile_rank_per90=stat.get("percentileRankPer90"),
        )

    stats_items = list(by_title_id.values())

    return FotMobPlayerStatsRaw(
        raw_artifact_path=str(path),
        player_id=str(player_id),
        season_entry_id=season_entry_id,
        stats_items=stats_items,
    )

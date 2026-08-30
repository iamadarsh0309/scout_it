from __future__ import annotations

import datetime as dt

from etl.models_raw.fotmob import FotMobPlayerRaw


def _info_by_key(raw: FotMobPlayerRaw) -> dict:
    return {item.translation_key: item for item in raw.player_information}


def normalize_player(raw: FotMobPlayerRaw) -> dict:
    info = _info_by_key(raw)

    date_of_birth = None
    if raw.birth_date_utc:
        date_of_birth = dt.datetime.fromisoformat(raw.birth_date_utc).date()

    preferred_foot = None
    foot_item = info.get("preferred_foot")
    if foot_item is not None and isinstance(foot_item.key, str):
        preferred_foot = foot_item.key

    nationality = info.get("country_sentencecase")
    nationality_code = nationality.country_code if nationality is not None else None

    height_item = info.get("height_sentencecase")
    height_cm = int(height_item.number_value) if height_item and height_item.number_value else None

    return {
        "canonical_name": raw.name,
        "date_of_birth": date_of_birth,
        "nationality": nationality_code,
        "preferred_foot": preferred_foot,
        "primary_position": raw.primary_position_key,
        "height_cm": height_cm,
    }


def normalize_market_values(raw: FotMobPlayerRaw) -> list[dict]:
    if not raw.market_values:
        return []
    out = []
    for point in raw.market_values.get("values", []):
        date_str = point.get("date")
        if not date_str:
            continue
        as_of_date = dt.datetime.fromisoformat(date_str).date()
        out.append(
            {
                "as_of_date": as_of_date,
                "value_eur": point.get("value"),
                "lower_bound_eur": point.get("lowerBound"),
                "upper_bound_eur": point.get("upperBound"),
                "valuation_source": point.get("source", ""),
                "club_source_name": point.get("teamName"),  # resolved to club_id by the loader
            }
        )
    return out


def normalize_attribute_profile(raw: FotMobPlayerRaw) -> dict | None:
    if not raw.traits:
        return None
    attributes = {item["key"]: item["value"] for item in raw.traits.get("items", []) if "key" in item}
    return {
        "comparison_group_key": raw.traits.get("key", ""),
        "attributes": attributes,
    }

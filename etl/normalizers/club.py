from __future__ import annotations

from etl.models_raw.fotmob import FotMobClubRaw


def normalize_club(raw: FotMobClubRaw) -> dict:
    """Returns plain field values for Club — country resolution (ISO3 -> Country row) happens
    in the loader, which owns find-or-create semantics against the DB."""
    return {
        "name": raw.name,
        "country_code": raw.country_code,
    }

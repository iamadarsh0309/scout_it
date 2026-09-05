from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    Club,
    Competition,
    Country,
    Player,
    PlayerAttributeProfile,
    PlayerMarketValue,
    PlayerSourceMapping,
    PlayerStats,
    Season,
)

# FotMob's own club "country" codes are football-association codes, not strict ISO 3166-1
# alpha-3 (e.g. "ENG" for England, which has no ISO country code of its own) -- mapped
# manually for the countries this project's coverage.yaml currently targets.
FOOTBALL_COUNTRY_CODES = {
    "ENG": "England",
    "ESP": "Spain",
    "BRA": "Brazil",
}


def get_or_create_country(session: Session, name: str) -> Country:
    country = session.scalar(select(Country).where(Country.name == name))
    if country is None:
        country = Country(name=name)
        session.add(country)
        session.flush()
    return country


def get_or_create_country_by_football_code(session: Session, code: str | None) -> Country | None:
    if not code:
        return None
    return get_or_create_country(session, resolve_football_country_name(code))


def resolve_football_country_name(code: str | None) -> str | None:
    """Player.nationality is a free-text column (not an FK), but should still store the
    same friendly name Club/Competition resolve their Country rows to (e.g. "England"),
    not FotMob's raw federation code ("ENG") -- keeps the two consistent/readable."""
    if not code:
        return None
    return FOOTBALL_COUNTRY_CODES.get(code, code)


def get_or_create_competition(session: Session, country: Country, name: str, tier: int | None) -> Competition:
    competition = session.scalar(
        select(Competition).where(Competition.country_id == country.id, Competition.name == name)
    )
    if competition is None:
        competition = Competition(country_id=country.id, name=name, tier=tier)
        session.add(competition)
        session.flush()
    return competition


def get_or_create_season(session: Session, competition: Competition, label: str) -> Season:
    season = session.scalar(select(Season).where(Season.competition_id == competition.id, Season.label == label))
    if season is None:
        season = Season(competition_id=competition.id, label=label)
        session.add(season)
        session.flush()
    return season


def find_club_by_name(session: Session, name: str | None) -> Club | None:
    """Lookup-only, no create -- used for incidental club references (e.g. a player's
    market-value history mentions clubs across their whole career, including youth teams,
    loan spells, and foreign clubs entirely outside this project's tracked scope). Creating
    a Club row for every such name would pollute the table meant to represent only the
    competitions/clubs coverage.yaml actually tracks (confirmed live: naively calling
    get_or_create_club_by_name from market-value normalization inflated Club from 20 rows
    to 613 across a real 572-player run)."""
    if not name:
        return None
    return session.scalar(select(Club).where(Club.name == name))


def get_or_create_club_by_name(session: Session, name: str, country: Country | None = None) -> Club:
    """Club has no source_id mapping yet (unlike Player, which has player_source_mapping) --
    single-source FotMob keys it by name for now. Revisit with a proper club_source_mapping
    table if/when a second source or name-collision risk (e.g. two "Real"-something clubs
    across countries) makes this unsafe."""
    club = session.scalar(select(Club).where(Club.name == name))
    if club is None:
        club = Club(name=name, country_id=country.id if country else None)
        session.add(club)
        session.flush()
    elif country is not None and club.country_id is None:
        club.country_id = country.id
    return club


def get_or_create_player_by_source(
    session: Session, source: str, source_player_id: str, source_name: str, fields: dict
) -> Player:
    """FotMob is the sole source, so identity resolution is direct: find-or-create via
    player_source_mapping, no fuzzy name/DOB matching needed (see etl/PLAN.md)."""
    mapping = session.scalar(
        select(PlayerSourceMapping).where(
            PlayerSourceMapping.source == source, PlayerSourceMapping.source_player_id == source_player_id
        )
    )
    if mapping is not None:
        player = session.get(Player, mapping.player_id)
        for key, value in fields.items():
            setattr(player, key, value)
        return player

    player = Player(**fields)
    session.add(player)
    session.flush()
    session.add(
        PlayerSourceMapping(
            player_id=player.id,
            source=source,
            source_player_id=source_player_id,
            source_name=source_name,
            match_confidence=None,
            match_method="seed",
        )
    )
    return player


def upsert_player_stats(
    session: Session,
    player: Player,
    source: str,
    competition: Competition,
    season: Season,
    club: Club,
    fetched_at: dt.datetime,
    fields: dict,
) -> PlayerStats:
    row = session.scalar(
        select(PlayerStats).where(
            PlayerStats.player_id == player.id,
            PlayerStats.source == source,
            PlayerStats.competition_id == competition.id,
            PlayerStats.season_id == season.id,
            PlayerStats.club_id == club.id,
        )
    )
    if row is None:
        row = PlayerStats(
            player_id=player.id,
            source=source,
            competition_id=competition.id,
            season_id=season.id,
            club_id=club.id,
            fetched_at=fetched_at,
            **fields,
        )
        session.add(row)
    else:
        row.fetched_at = fetched_at
        for key, value in fields.items():
            setattr(row, key, value)
    return row


def upsert_market_values(session: Session, player: Player, source: str, points: list[dict]) -> None:
    for point in points:
        point = dict(point)
        club_name = point.pop("club_source_name", None)
        club = find_club_by_name(session, club_name)

        row = session.scalar(
            select(PlayerMarketValue).where(
                PlayerMarketValue.player_id == player.id,
                PlayerMarketValue.source == source,
                PlayerMarketValue.as_of_date == point["as_of_date"],
            )
        )
        if row is None:
            row = PlayerMarketValue(
                player_id=player.id,
                source=source,
                club_id_at_valuation=club.id if club else None,
                **point,
            )
            session.add(row)
        else:
            for key, value in point.items():
                setattr(row, key, value)
            row.club_id_at_valuation = club.id if club else row.club_id_at_valuation


def upsert_attribute_profile(session: Session, player: Player, source: str, profile: dict) -> None:
    row = session.scalar(
        select(PlayerAttributeProfile).where(
            PlayerAttributeProfile.player_id == player.id,
            PlayerAttributeProfile.source == source,
            PlayerAttributeProfile.comparison_group_key == profile["comparison_group_key"],
        )
    )
    if row is None:
        row = PlayerAttributeProfile(
            player_id=player.id,
            source=source,
            comparison_group_key=profile["comparison_group_key"],
            attributes=profile["attributes"],
            computed_at=dt.datetime.now(dt.UTC),
        )
        session.add(row)
    else:
        row.attributes = profile["attributes"]
        row.computed_at = dt.datetime.now(dt.UTC)

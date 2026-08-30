from __future__ import annotations

import datetime as dt

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Country(Base):
    __tablename__ = "country"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)

    competitions: Mapped[list[Competition]] = relationship(back_populates="country")


class Competition(Base):
    __tablename__ = "competition"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    country_id: Mapped[int] = mapped_column(ForeignKey("country.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    tier: Mapped[int | None] = mapped_column(Integer, nullable=True)

    country: Mapped[Country] = relationship(back_populates="competitions")
    seasons: Mapped[list[Season]] = relationship(back_populates="competition")

    __table_args__ = (UniqueConstraint("country_id", "name", name="uq_competition_country_name"),)


class Season(Base):
    __tablename__ = "season"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competition.id"), nullable=False)
    label: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "2025-2026"

    competition: Mapped[Competition] = relationship(back_populates="seasons")

    __table_args__ = (UniqueConstraint("competition_id", "label", name="uq_season_competition_label"),)


class Club(Base):
    __tablename__ = "club"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    country_id: Mapped[int | None] = mapped_column(ForeignKey("country.id"), nullable=True)


class Player(Base):
    """Canonical player identity — deduplicated across sources via player_source_mapping."""

    __tablename__ = "player"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    canonical_name: Mapped[str] = mapped_column(String(128), nullable=False)
    date_of_birth: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    nationality: Mapped[str | None] = mapped_column(String(64), nullable=True)
    preferred_foot: Mapped[str | None] = mapped_column(String(16), nullable=True)
    primary_position: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """Machine-friendly position key (e.g. "rightwinger"), not the human label -- some
    labels (e.g. "Attacking Midfielder") are longer than a short varchar comfortably fits."""
    height_cm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source_mappings: Mapped[list[PlayerSourceMapping]] = relationship(back_populates="player")


class PlayerSourceMapping(Base):
    """Links a canonical player_id to a per-source identity, per ProjectPlan.md section 4."""

    __tablename__ = "player_source_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_player_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    match_confidence: Mapped[float | None] = mapped_column(Numeric(4, 3), nullable=True)
    match_method: Mapped[str] = mapped_column(String(32), nullable=False)  # 'seed' | 'auto' | 'manual_review'
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    player: Mapped[Player] = relationship(back_populates="source_mappings")

    __table_args__ = (UniqueConstraint("source", "source_player_id", name="uq_source_player"),)


class PlayerClubHistory(Base):
    __tablename__ = "player_club_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"), nullable=False)
    club_id: Mapped[int] = mapped_column(ForeignKey("club.id"), nullable=False)
    season_id: Mapped[int] = mapped_column(ForeignKey("season.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint("player_id", "club_id", "season_id", "source", name="uq_player_club_season_source"),
    )


class PlayerCompetitionHistory(Base):
    __tablename__ = "player_competition_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"), nullable=False)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competition.id"), nullable=False)
    season_id: Mapped[int] = mapped_column(ForeignKey("season.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "player_id", "competition_id", "season_id", "source", name="uq_player_competition_season_source"
        ),
    )


class PlayerStats(Base):
    """Per-source stat lines are kept side by side, never merged — see ProjectPlan.md Pipeline B
    for cross-source reconciliation; Pipeline A only stores what each source reported."""

    __tablename__ = "player_stats"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    competition_id: Mapped[int] = mapped_column(ForeignKey("competition.id"), nullable=False)
    season_id: Mapped[int] = mapped_column(ForeignKey("season.id"), nullable=False)
    club_id: Mapped[int] = mapped_column(ForeignKey("club.id"), nullable=False)

    appearances: Mapped[int | None] = mapped_column(Integer, nullable=True)
    minutes_played: Mapped[int | None] = mapped_column(Integer, nullable=True)
    goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assists: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stats: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    """Everything not promoted to a first-class column (xg, xa, progressive_passes, tackles, ...)
    lives here as source-native key/value pairs until Pipeline B defines the normalized feature set."""

    fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "player_id", "source", "competition_id", "season_id", "club_id", name="uq_player_stats_natural_key"
        ),
    )


class PlayerMarketValue(Base):
    """Time series of estimated market value, e.g. FotMob's SciSports-sourced valuation
    history — not in ProjectPlan.md's original data model, added because it's directly
    useful for scouting search/filtering (see etl/PLAN.md)."""

    __tablename__ = "player_market_value"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    as_of_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    value_eur: Mapped[int] = mapped_column(Integer, nullable=False)
    lower_bound_eur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    upper_bound_eur: Mapped[int | None] = mapped_column(Integer, nullable=True)
    valuation_source: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "scisports"
    club_id_at_valuation: Mapped[int | None] = mapped_column(ForeignKey("club.id"), nullable=True)

    __table_args__ = (UniqueConstraint("player_id", "source", "as_of_date", name="uq_player_market_value"),)


class PlayerAttributeProfile(Base):
    """Percentile-vs-positional-peers comparison (e.g. FotMob's radar-chart traits: chances
    created, aerial duels, defensive actions, ...). Position-relative, not a raw per-90 stat,
    so kept separate from PlayerStats."""

    __tablename__ = "player_attribute_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    comparison_group_key: Mapped[str] = mapped_column(String(64), nullable=False)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    computed_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("player_id", "source", "comparison_group_key", name="uq_player_attribute_profile"),
    )


class ScrapeLedger(Base):
    """Tracks what has been fetched from each source, for skip-if-fresh and skip-if-unchanged."""

    __tablename__ = "scrape_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)  # competition|season|club|squad|player|player_stats
    source_entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_path: Mapped[str] = mapped_column(Text, nullable=False)
    last_fetched_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok|failed|blocked

    __table_args__ = (
        UniqueConstraint("source", "entity_type", "source_entity_id", name="uq_ledger_entity"),
    )


class EntityResolutionReview(Base):
    """Human-in-the-loop review queue for player matches below the auto-link confidence threshold."""

    __tablename__ = "entity_resolution_review"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    candidate_player_id: Mapped[int] = mapped_column(ForeignKey("player.id"), nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_player_id: Mapped[str] = mapped_column(String(64), nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    score: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    score_breakdown: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")  # pending|confirmed|rejected
    reviewed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FailedFetch(Base):
    """Ledger of fetches that exhausted retries — reviewed manually rather than silently dropped."""

    __tablename__ = "failed_fetch"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str] = mapped_column(Text, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

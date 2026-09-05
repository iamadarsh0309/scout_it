from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = PROJECT_ROOT / "data"
CONFIG_ROOT = Path(__file__).resolve().parent


class RateLimitConfig(BaseModel):
    min_delay_seconds: float
    max_delay_seconds: float


class SourceConfig(BaseModel):
    concurrency: int
    rate_limit: RateLimitConfig
    stealth: bool = True


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SCOUT_IT_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://scout_it:scout_it@localhost:5432/scout_itproj"
    """Primary DB connection -- local Postgres by default; set SCOUT_IT_DATABASE_URL to a
    Supabase session-pooler URL to make the cloud DB the default everywhere."""

    supabase_database_url: str | None = None
    """Optional second target for tools that need to explicitly address the cloud DB
    regardless of what database_url currently points at (e.g. scripts/data_visualizer.py
    --target supabase). Set via SCOUT_IT_SUPABASE_DATABASE_URL. Not used by the main app."""

    raw_data_dir: Path = DATA_ROOT / "raw"
    normalized_data_dir: Path = DATA_ROOT / "normalized"
    processed_data_dir: Path = DATA_ROOT / "processed"

    # Freshness window before a player is considered stale and re-fetched.
    player_freshness_days: int = 7

    # Entity resolution thresholds (see etl/entity_resolution/matcher.py).
    auto_link_threshold: float = 0.92
    review_queue_threshold: float = 0.65

    sources: dict[str, SourceConfig] = Field(
        default_factory=lambda: {
            "fotmob": SourceConfig(
                concurrency=2,
                rate_limit=RateLimitConfig(min_delay_seconds=3.0, max_delay_seconds=6.0),
            ),
        }
    )


class SeasonCoverage(BaseModel):
    label: str
    active: bool
    source_season_ids: dict[str, str]


class CompetitionCoverage(BaseModel):
    slug: str
    country: str
    tier: int
    name: str
    active: bool
    sources: dict[str, dict[str, str]]
    seasons: list[SeasonCoverage]

    def source_competition_id(self, source: str) -> str:
        return self.sources[source]["competition_id"]


class CoverageConfig(BaseModel):
    version: int
    source_priority: list[str]
    competitions: list[CompetitionCoverage]

    def active_competitions(self) -> list[CompetitionCoverage]:
        return [c for c in self.competitions if c.active]

    def by_slug(self, slug: str) -> CompetitionCoverage:
        for comp in self.competitions:
            if comp.slug == slug:
                return comp
        raise KeyError(f"No competition with slug={slug!r} in coverage.yaml")

    @classmethod
    def load(cls, path: Path | None = None) -> CoverageConfig:
        path = path or (CONFIG_ROOT / "coverage.yaml")
        with open(path) as f:
            raw = yaml.safe_load(f)
        return cls.model_validate(raw)


def load_name_aliases(path: Path | None = None) -> dict[str, list[str]]:
    path = path or (CONFIG_ROOT / "name_aliases.yaml")
    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    return raw.get("aliases", {}) or {}


settings = Settings()

from __future__ import annotations

import asyncio
import logging

import click
from sqlalchemy.orm import Session

from config.settings import CoverageConfig, settings
from db.models import FailedFetch
from db.session import get_session
from scraper.adapters.fotmob import FotMobAdapter
from scraper.adapters.sofascore import SofascoreAdapter
from scraper.base import BaseSourceAdapter, DiscoveredCompetition, DiscoveredSeason
from scraper.browser import BrowserSessionManager
from scraper.fingerprint import get_ledger_entry, is_fresh, record_fetch
from scraper.rate_limit import RateLimiter
from scraper.raw_store import LocalFileRawStore
from scraper.retry import BlockedError, FetchError

logger = logging.getLogger(__name__)

STAGES = ["discover-clubs", "discover-squads", "fetch-players", "fetch-stats", "all"]
ADAPTERS = {"fotmob": FotMobAdapter, "sofascore": SofascoreAdapter}


def _competition_and_season(coverage: CoverageConfig, competition_slug: str, season_label: str | None):
    comp = coverage.by_slug(competition_slug)
    if not comp.active:
        raise click.ClickException(f"Competition {competition_slug!r} is not active in coverage.yaml")
    seasons = [s for s in comp.seasons if s.active]
    if season_label:
        seasons = [s for s in seasons if s.label == season_label]
    if not seasons:
        raise click.ClickException(f"No active season found for {competition_slug!r} (season={season_label!r})")
    return comp, seasons


def _build_adapter(source: str, browser: BrowserSessionManager, raw_store: LocalFileRawStore, coverage: CoverageConfig) -> BaseSourceAdapter:
    source_cfg = settings.sources[source]
    rate_limiter = RateLimiter(
        source_cfg.rate_limit.min_delay_seconds, source_cfg.rate_limit.max_delay_seconds
    )
    return ADAPTERS[source](browser, raw_store, rate_limiter, coverage)


async def _fetch_with_freshness(
    session: Session,
    entity_type: str,
    source: str,
    source_entity_id: str,
    fetch_fn,
    force: bool,
):
    """Shared skip-if-fresh / record-ledger / failure-logging wrapper used for every
    player-level fetch, implementing the spec's 'avoid re-discovering the same player'
    requirement via scrape_ledger."""
    if not force:
        entry = get_ledger_entry(session, source, entity_type, source_entity_id)
        if is_fresh(entry, settings.player_freshness_days):
            logger.info("Skipping fresh %s %s/%s", entity_type, source, source_entity_id)
            return None

    try:
        result = await fetch_fn()
    except (BlockedError, FetchError) as exc:
        session.add(
            FailedFetch(
                source=source,
                entity_type=entity_type,
                source_entity_id=source_entity_id,
                url=getattr(exc, "url", ""),
                error=str(exc),
                attempts=3,
            )
        )
        session.commit()
        logger.warning("Giving up on %s %s/%s: %s", entity_type, source, source_entity_id, exc)
        return None

    # adapters already call raw_store.write(); the ledger only needs a pointer + hash/status
    record_fetch(session, result, raw_path=result.url, status="ok")
    session.commit()
    return result


async def _crawl(source: str, competition_slug: str, season_label: str | None, stage: str, force: bool):
    coverage = CoverageConfig.load()
    comp_cfg, season_cfgs = _competition_and_season(coverage, competition_slug, season_label)

    raw_store = LocalFileRawStore(settings.raw_data_dir)
    session = get_session()

    concurrency = {s: cfg.concurrency for s, cfg in settings.sources.items()}
    stealth = {s: cfg.stealth for s, cfg in settings.sources.items()}

    async with BrowserSessionManager(concurrency, stealth) as browser:
        adapter = _build_adapter(source, browser, raw_store, coverage)

        competition = DiscoveredCompetition(
            source=source,
            source_competition_id=comp_cfg.source_competition_id(source),
            name=comp_cfg.name,
            country=comp_cfg.country,
            tier=comp_cfg.tier,
            url="",
        )

        for season_cfg in season_cfgs:
            if source not in season_cfg.source_season_ids:
                logger.warning("No %s season id for %s %s, skipping", source, competition_slug, season_cfg.label)
                continue
            season = DiscoveredSeason(
                source=source,
                source_competition_id=competition.source_competition_id,
                source_season_id=season_cfg.source_season_ids[source],
                label=season_cfg.label,
                url="",
            )

            clubs = []
            async for club in adapter.discover_clubs(competition, season):
                clubs.append(club)
            logger.info("Discovered %d clubs for %s/%s (%s)", len(clubs), competition_slug, season.label, source)

            if stage == "discover-clubs":
                continue

            for club in clubs:
                players = []
                async for player in adapter.discover_squad(club, season):
                    players.append(player)
                logger.info("Discovered %d players for club %s (%s)", len(players), club.name, source)

                if stage == "discover-squads":
                    continue

                for player in players:
                    if stage in ("fetch-players", "all"):
                        await _fetch_with_freshness(
                            session, "player", source, player.source_player_id,
                            lambda p=player: adapter.fetch_player_profile(p), force,
                        )
                    if stage in ("fetch-stats", "all"):
                        await _fetch_with_freshness(
                            session, "player_stats", source, player.source_player_id,
                            lambda p=player, s=season: adapter.fetch_player_stats(p, s), force,
                        )

    session.close()


@click.group()
def cli():
    """Pipeline A data collection tool."""


@cli.command()
@click.option("--source", type=click.Choice(list(ADAPTERS) + ["all"]), required=True)
@click.option("--competition", "competition_slug", required=True, help="Slug from config/coverage.yaml")
@click.option("--season", "season_label", default=None, help="Season label, e.g. 2025-2026")
@click.option("--stage", type=click.Choice(STAGES), default="all")
@click.option("--force", is_flag=True, default=False, help="Bypass the freshness/ledger skip")
def crawl(source: str, competition_slug: str, season_label: str | None, stage: str, force: bool):
    sources = list(ADAPTERS) if source == "all" else [source]
    for s in sources:
        asyncio.run(_crawl(s, competition_slug, season_label, stage, force))


if __name__ == "__main__":
    cli()

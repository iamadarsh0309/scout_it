from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

import click

from config.settings import CoverageConfig, settings
from db.session import get_session
from etl.load.loaders import (
    get_or_create_club_by_name,
    get_or_create_competition,
    get_or_create_country,
    get_or_create_country_by_football_code,
    get_or_create_player_by_source,
    get_or_create_season,
    resolve_football_country_name,
    upsert_attribute_profile,
    upsert_market_values,
    upsert_player_stats,
)
from etl.normalizers.club import normalize_club
from etl.normalizers.player import (
    normalize_attribute_profile,
    normalize_market_values,
    normalize_player,
)
from etl.normalizers.player_stats import normalize_player_stats
from etl.parsers.fotmob_parser import parse_club, parse_player, parse_player_stats

logger = logging.getLogger(__name__)

SOURCE = "fotmob"  # sole source; see etl/PLAN.md and the Sofascore-removal decision


def _latest_file(directory: Path, suffix: str) -> Path | None:
    if not directory.is_dir():
        return None
    files = sorted(p for p in directory.iterdir() if p.name.endswith(suffix) and not p.name.endswith(".meta.json"))
    return files[-1] if files else None


def _discovered_club_ids(raw_root: Path) -> list[str]:
    club_root = raw_root / "club"
    if not club_root.is_dir():
        return []
    return sorted(p.name for p in club_root.iterdir() if p.is_dir())


def run_etl(competition_slug: str, season_label: str) -> dict:
    """Reads only data/raw/fotmob/ on disk -- never the network -- so this can be rerun
    freely to pick up parser/normalizer fixes without re-scraping. Returns a summary dict
    for logging/reporting."""
    coverage = CoverageConfig.load()
    comp_cfg = coverage.by_slug(competition_slug)
    season_cfg = next((s for s in comp_cfg.seasons if s.label == season_label), None)
    if season_cfg is None:
        raise click.ClickException(f"No season {season_label!r} configured for {competition_slug!r}")

    raw_root = settings.raw_data_dir / SOURCE
    session = get_session()

    competition_country = get_or_create_country(session, comp_cfg.country)
    competition = get_or_create_competition(session, competition_country, comp_cfg.name, comp_cfg.tier)
    season = get_or_create_season(session, competition, season_label)
    session.commit()

    summary = {
        "clubs_processed": 0,
        "players_processed": 0,
        "players_with_stats": 0,
        "players_missing_stats_file": 0,
        "market_value_rows": 0,
        "attribute_profiles": 0,
        "parse_errors": [],
    }

    club_ids = _discovered_club_ids(raw_root)
    logger.info("Discovered %d club raw directories under %s", len(club_ids), raw_root)

    for club_id in club_ids:
        club_html = _latest_file(raw_root / "club" / club_id, ".html")
        if club_html is None:
            logger.warning("No club raw file for club_id=%s, skipping", club_id)
            continue

        try:
            raw_club = parse_club(club_html)
        except ValueError as exc:
            logger.error("Failed to parse club %s: %s", club_id, exc)
            summary["parse_errors"].append(f"club:{club_id}: {exc}")
            continue

        club_country = get_or_create_country_by_football_code(session, raw_club.country_code)
        club_fields = normalize_club(raw_club)
        club = get_or_create_club_by_name(session, club_fields["name"], club_country)
        session.commit()
        summary["clubs_processed"] += 1
        logger.info("Club %s (%s): %d squad groups", club.name, club_id, len(raw_club.squad_groups))

        member_ids = []
        for group in raw_club.squad_groups:
            if group.get("title") == "coach":
                continue
            for member in group.get("members", []):
                if member.get("id") is not None:
                    member_ids.append(str(member["id"]))

        for player_id in member_ids:
            player_html = _latest_file(raw_root / "player" / player_id, ".html")
            if player_html is None:
                logger.warning("No player raw file for player_id=%s (club=%s), skipping", player_id, club.name)
                continue

            try:
                raw_player = parse_player(player_html)
            except ValueError as exc:
                logger.error("Failed to parse player %s: %s", player_id, exc)
                summary["parse_errors"].append(f"player:{player_id}: {exc}")
                continue

            player_fields = normalize_player(raw_player)
            player_fields["nationality"] = resolve_football_country_name(player_fields["nationality"])
            player = get_or_create_player_by_source(
                session, source=SOURCE, source_player_id=player_id, source_name=raw_player.name, fields=player_fields
            )
            session.flush()

            market_points = normalize_market_values(raw_player)
            upsert_market_values(session, player, SOURCE, market_points)
            summary["market_value_rows"] += len(market_points)

            attribute_profile = normalize_attribute_profile(raw_player)
            if attribute_profile:
                upsert_attribute_profile(session, player, SOURCE, attribute_profile)
                summary["attribute_profiles"] += 1

            stats_json = _latest_file(raw_root / "player_stats" / player_id, ".json")
            if stats_json is None:
                logger.info("No player_stats raw file for player_id=%s (%s) -- profile-only", player_id, raw_player.name)
                summary["players_missing_stats_file"] += 1
            else:
                try:
                    raw_stats = parse_player_stats(stats_json)
                    stats_fields = normalize_player_stats(raw_stats)
                    upsert_player_stats(
                        session, player, SOURCE, competition, season, club,
                        fetched_at=dt.datetime.now(dt.UTC), fields=stats_fields,
                    )
                    summary["players_with_stats"] += 1
                except (ValueError, KeyError) as exc:
                    logger.error("Failed to parse/load player_stats for %s: %s", player_id, exc)
                    summary["parse_errors"].append(f"player_stats:{player_id}: {exc}")

            session.commit()
            summary["players_processed"] += 1

    session.close()
    return summary


@click.group()
def cli():
    """Pipeline A/B ETL: parse raw FotMob HTML/JSON -> normalize -> load into PostgreSQL."""


@cli.command()
@click.option("--competition", "competition_slug", required=True, help="Slug from config/coverage.yaml")
@click.option("--season", "season_label", required=True, help="Season label, e.g. 2025-2026")
def run(competition_slug: str, season_label: str):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_etl(competition_slug, season_label)
    logger.info("SUMMARY: %s", summary)


if __name__ == "__main__":
    cli()

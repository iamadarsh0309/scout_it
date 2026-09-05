"""Tabular report of the current contents of the scout_it database.

Works against either the local Postgres instance or a Supabase instance -- both are just
values of an env-driven connection URL (SCOUT_IT_DATABASE_URL / SCOUT_IT_SUPABASE_DATABASE_URL,
see config/settings.py), so this script builds its own engine per --target rather than reusing
db/session.py's single import-time-bound engine.

Usage:
    uv run python -m scripts.data_visualizer --target local
    uv run python -m scripts.data_visualizer --target supabase
"""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from config.settings import settings
from db.models import (
    Club,
    Competition,
    Country,
    EntityResolutionReview,
    FailedFetch,
    Player,
    PlayerAttributeProfile,
    PlayerClubHistory,
    PlayerCompetitionHistory,
    PlayerMarketValue,
    PlayerSourceMapping,
    PlayerStats,
    ScrapeLedger,
    Season,
)

console = Console()

ROW_COUNT_MODELS = [
    Country,
    Competition,
    Season,
    Club,
    Player,
    PlayerSourceMapping,
    PlayerStats,
    PlayerMarketValue,
    PlayerAttributeProfile,
    PlayerClubHistory,
    PlayerCompetitionHistory,
    ScrapeLedger,
    FailedFetch,
    EntityResolutionReview,
]


def _mask_url(url: str) -> str:
    """Never print a real password to the terminal, even a local dev one."""
    parts = urlsplit(url)
    if parts.password:
        netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
    else:
        netloc = parts.netloc
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


def _resolve_url(target: str) -> str:
    if target == "local":
        return settings.database_url
    if target == "supabase":
        if not settings.supabase_database_url:
            raise click.ClickException(
                "SCOUT_IT_SUPABASE_DATABASE_URL is not set. Set it in .env once the Supabase "
                "project exists (see .env.example)."
            )
        return settings.supabase_database_url
    raise click.ClickException(f"Unknown target {target!r}")


def _session_for(target: str) -> tuple[Session, str]:
    url = _resolve_url(target)
    engine = create_engine(url, future=True, pool_pre_ping=True, connect_args={"connect_timeout": 10})
    return sessionmaker(bind=engine, future=True)(), url


def _row_counts_table(session: Session) -> Table:
    table = Table(title="Row Counts", show_lines=False)
    table.add_column("Table")
    table.add_column("Rows", justify="right")
    for model in ROW_COUNT_MODELS:
        count = session.query(model).count()
        table.add_row(model.__tablename__, str(count))
    return table


def _completeness_table(session: Session) -> Table:
    total_players = session.query(Player).count()
    with_stats = session.execute(text("SELECT count(DISTINCT player_id) FROM player_stats")).scalar_one()
    with_market_value = session.execute(
        text("SELECT count(DISTINCT player_id) FROM player_market_value")
    ).scalar_one()
    with_attributes = session.execute(
        text("SELECT count(DISTINCT player_id) FROM player_attribute_profile")
    ).scalar_one()

    table = Table(title="Player Data Completeness", show_lines=False)
    table.add_column("Metric")
    table.add_column("Players", justify="right")
    table.add_column("Coverage", justify="right")

    def pct(n: int) -> str:
        return f"{(n / total_players * 100):.1f}%" if total_players else "n/a"

    table.add_row("Total players", str(total_players), "100.0%")
    table.add_row("With season stats", str(with_stats), pct(with_stats))
    table.add_row("With market value history", str(with_market_value), pct(with_market_value))
    table.add_row("With attribute profile", str(with_attributes), pct(with_attributes))
    return table


def _stats_by_club_table(session: Session) -> Table:
    rows = session.execute(
        text(
            """
            SELECT c.name, count(DISTINCT ps.player_id) AS players_with_stats
            FROM player_stats ps
            JOIN club c ON c.id = ps.club_id
            GROUP BY c.name
            ORDER BY players_with_stats DESC, c.name
            """
        )
    ).all()

    table = Table(
        title="Players With Season Stats, by Club",
        caption="Partial by design: only players with a fetched player_stats row are linked "
        "to a club here (Player itself has no direct club FK; PlayerClubHistory isn't "
        "populated by the current ETL). Not a total squad-size count.",
    )
    table.add_column("Club")
    table.add_column("Players w/ Stats", justify="right")
    for name, count in rows:
        table.add_row(name, str(count))
    return table


def _top_scorers_table(session: Session) -> Table:
    rows = session.execute(
        text(
            """
            SELECT p.canonical_name, c.name AS club, ps.goals, ps.assists,
                   ps.minutes_played, ps.appearances,
                   (ps.stats -> 'rating' ->> 'value') AS rating
            FROM player_stats ps
            JOIN player p ON p.id = ps.player_id
            JOIN club c ON c.id = ps.club_id
            WHERE ps.goals IS NOT NULL
            ORDER BY ps.goals DESC, ps.assists DESC NULLS LAST
            LIMIT 10
            """
        )
    ).all()

    table = Table(title="Top Scorers (2025-2026 Premier League)")
    table.add_column("Player")
    table.add_column("Club")
    table.add_column("Goals", justify="right")
    table.add_column("Assists", justify="right")
    table.add_column("Minutes", justify="right")
    table.add_column("Apps", justify="right")
    table.add_column("Rating", justify="right")
    for name, club, goals, assists, minutes, apps, rating in rows:
        table.add_row(
            name, club, str(goals), str(assists or "-"), str(minutes or "-"), str(apps or "-"), rating or "-"
        )
    return table


def _market_value_leaders_table(session: Session) -> Table:
    rows = session.execute(
        text(
            """
            SELECT p.canonical_name, mv.value_eur, mv.as_of_date
            FROM (
                SELECT DISTINCT ON (player_id) player_id, value_eur, as_of_date
                FROM player_market_value
                ORDER BY player_id, as_of_date DESC
            ) mv
            JOIN player p ON p.id = mv.player_id
            ORDER BY mv.value_eur DESC
            LIMIT 10
            """
        )
    ).all()

    table = Table(title="Market Value Leaders (latest known valuation)")
    table.add_column("Player")
    table.add_column("Value (EUR)", justify="right")
    table.add_column("As Of")
    for name, value_eur, as_of_date in rows:
        table.add_row(name, f"{value_eur:,}", str(as_of_date))
    return table


def _scrape_ledger_table(session: Session) -> Table | None:
    rows = session.execute(
        text(
            """
            SELECT source, entity_type, last_status, count(*)
            FROM scrape_ledger
            GROUP BY source, entity_type, last_status
            ORDER BY source, entity_type, last_status
            """
        )
    ).all()
    if not rows:
        return None

    table = Table(title="Scrape Ledger Summary")
    table.add_column("Source")
    table.add_column("Entity Type")
    table.add_column("Status")
    table.add_column("Count", justify="right")
    for source, entity_type, status, count in rows:
        table.add_row(source, entity_type, status, str(count))
    return table


@click.command()
@click.option("--target", type=click.Choice(["local", "supabase"]), default="local")
def main(target: str):
    session, url = _session_for(target)
    console.print(Panel(f"Target: [bold]{target}[/bold]\nConnection: {_mask_url(url)}", title="scout_it data report"))

    console.print(_row_counts_table(session))
    console.print(_completeness_table(session))
    console.print(_stats_by_club_table(session))

    top_scorers = _top_scorers_table(session)
    if top_scorers.row_count:
        console.print(top_scorers)
    else:
        console.print("[dim]No player_stats rows with goals yet -- skipping top scorers.[/dim]")

    mv_leaders = _market_value_leaders_table(session)
    if mv_leaders.row_count:
        console.print(mv_leaders)
    else:
        console.print("[dim]No player_market_value rows yet -- skipping market value leaders.[/dim]")

    ledger_table = _scrape_ledger_table(session)
    if ledger_table is not None:
        console.print(ledger_table)

    session.close()


if __name__ == "__main__":
    main()

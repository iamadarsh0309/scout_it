from __future__ import annotations

import datetime as dt

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import ScrapeLedger
from scraper.base import RawFetchResult
from scraper.raw_store import content_hash


def get_ledger_entry(
    session: Session, source: str, entity_type: str, source_entity_id: str
) -> ScrapeLedger | None:
    stmt = select(ScrapeLedger).where(
        ScrapeLedger.source == source,
        ScrapeLedger.entity_type == entity_type,
        ScrapeLedger.source_entity_id == source_entity_id,
    )
    return session.scalars(stmt).first()


def is_fresh(entry: ScrapeLedger | None, freshness_days: int) -> bool:
    """Used for the player-ID cache: skip re-fetching an entity fetched successfully within
    the freshness window, even if reached again via a different traversal path."""
    if entry is None or entry.last_status != "ok":
        return False
    age = dt.datetime.now(dt.UTC) - entry.last_fetched_at
    return age <= dt.timedelta(days=freshness_days)


def record_fetch(
    session: Session,
    result: RawFetchResult,
    raw_path: str,
    status: str = "ok",
) -> tuple[ScrapeLedger, bool]:
    """Upserts the scrape_ledger row for this entity. Returns (entry, content_unchanged) —
    content_unchanged lets callers skip re-parsing when the new fetch is byte-identical to
    the last one, satisfying the 'avoid re-discovering the same player' requirement."""
    body = (result.html or "").encode("utf-8") if result.content_type == "html" else b""
    if result.content_type != "html":
        import json

        body = json.dumps(result.json_payloads or []).encode("utf-8")
    new_hash = content_hash(body)

    entry = get_ledger_entry(session, result.source, result.entity_type, result.source_entity_id)
    content_unchanged = entry is not None and entry.last_content_hash == new_hash

    if entry is None:
        entry = ScrapeLedger(
            source=result.source,
            entity_type=result.entity_type,
            source_entity_id=result.source_entity_id,
            raw_path=raw_path,
            last_fetched_at=result.fetched_at,
            last_content_hash=new_hash,
            last_status=status,
        )
        session.add(entry)
    else:
        entry.raw_path = raw_path
        entry.last_fetched_at = result.fetched_at
        entry.last_content_hash = new_hash
        entry.last_status = status

    return entry, content_unchanged

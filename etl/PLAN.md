# ETL Plan — FotMob Raw HTML to PostgreSQL

Scope: Parser → Validation → Normalization → Entity Resolution → Load, per `ProjectPlan.md` Pipeline A/B. FotMob is the sole source (Sofascore was removed). This plan is grounded in the actual raw files collected during the full Premier League crawl (20 clubs, 572 players, `data/raw/fotmob/`), not assumptions — every structure below was read directly off disk.

## Critical finding — `player_stats` entity type is currently broken

`scraper/adapters/fotmob.py`'s `fetch_player_stats` re-navigates to the exact same URL as `fetch_player_profile` (`/players/{id}/-`). Diffed the two raw files for the same player byte-for-byte after JSON-parsing: **identical**. Two consequences:

1. Every player's `player_stats` fetch is a wasted duplicate request — half the FotMob request budget (572 of ~1144 requests) produced no new data.
2. Even if it weren't a duplicate, the profile page's embedded season data (`mainLeague`, `firstSeasonStats`) reflects whatever FotMob currently considers the "current" season (`2026/2027`, since site time has rolled past the 2025-2026 season), **not** the 2025-2026 season this dataset is supposed to represent. `statSeasons` on the profile page is only an **index** — `{seasonName, tournaments: [{tournamentId, entryId}]}` — it holds no stat values, just pointers.

**Verified fix, live**: `GET https://www.fotmob.com/api/data/playerStats?playerId={id}&seasonId={entryId}` (a different, unauthenticated host from the gated `apigw` API) returns the correct season/tournament-scoped stats, keyed by the `entryId` found in that player's own `statSeasons` index (e.g. Saka's 2025-2026 Premier League `entryId` is `"1-0"`). Confirmed against player 961995 (Saka): returns `sectionOrder`, `shotmap`, `statsSection`, `topStatCard`, `heatmap`, `keeperShotmap` — `statsSection.items[].items[]` contains exactly what we need: `{localizedTitleId, title, statValue, per90, percentileRank, percentileRankPer90}` per metric (goals, expected_goals, expected_goals_on_target, shots, non_penalty_xg, ...). FotMob has already computed per-90 and percentile-vs-position-peers for us.

**Required scraper change before ETL can produce correct per-season stats** (not just an ETL/parsing fix — the raw data itself needs to change):
- `fetch_player_stats(player, season)` must become two-step: (1) if not already known, read the player's own profile raw file (already on disk from `fetch_player_profile`) to find the `entryId` matching `tournamentId == season.source_competition_id` and `seasonName` matching the target season label; (2) fetch `https://www.fotmob.com/api/data/playerStats?playerId={id}&seasonId={entryId}` and store *that* as the `player_stats` raw artifact instead of re-fetching the profile page.
- This needs a full re-crawl of the `player_stats` entity type for all 572 players once fixed (the `player` entity type / profile fetches are unaffected and don't need re-fetching).
- Filed as a blocker for accurate `PlayerStats` normalization — the parser/normalizer design below assumes this fix lands; until then, `PlayerStats` can only be populated from `mainLeague`'s current-season summary as a stopgap (fewer metrics, wrong season).

## Raw data inventory (by entity_type)

All paths under `data/raw/fotmob/{entity_type}/{source_entity_id}/{timestamp}.html` + `.meta.json` sidecar (`source`, `entity_type`, `source_entity_id`, `url`, `http_status`, `fetched_at`, `content_hash`).

### `competition` (1 file — the league table page)

`__NEXT_DATA__.props.pageProps.data` (top-level, not nested under `fallback`). Relevant for ETL: `allAvailableSeasons` (list of season label strings), `selectedSeason`, and the standings table rows already exploited by the scraper (`pageUrl` starting `/teams/{id}/...`, `name`, `played/wins/draws/losses/pts`). Not much new for ETL beyond what the scraper already used for discovery — this entity type mainly exists to seed `Club` discovery, not to populate its own DB row set beyond `Competition`/`Season`.

### `club` (20 files, one per club — squad pages)

**Different top-level shape from `player`**: `__NEXT_DATA__.props.pageProps` here only has `fallback`/`translations` at the top; the real payload is nested at `props.pageProps.fallback["team-{club_id}"]`, keyed dynamically by the club's own id. Under that key:
- `details`: `{id, type, name, shortName, country (ISO3, e.g. "ENG"), latestSeason, gender, sportsTeamJSONLD: {logo url, ...}, faqJSONLD: {...stadium name/location/capacity via free text in Q&A...}}` — gives us `Club.name`, `Club.country` (via ISO3 → `Country` lookup), and a logo URL.
- `squad.squad`: list of position-group dicts (`title`: `"coach"`/`"keepers"`/`"defenders"`/`"midfielders"`/`"attackers"`, each with `members: [...]`) — this is what the scraper already extracts generically at crawl time; the parser will read it precisely via this exact path (`fallback["team-{id}"].squad.squad`) rather than the scraper's intentionally-generic recursive search (which exists only to survive minor layout drift during discovery, not for precise re-parsing).
- `allAvailableSeasons`, `table`, `transfers`, `fixtures`, `history` also present but out of scope for MVP `Club`/`PlayerClubHistory` — `transfers` is worth a future look for `PlayerClubHistory` transfer-window boundaries, not now.

### `player` (572 files — profile pages)

`__NEXT_DATA__.props.pageProps.data` (flat, not nested under `fallback` like club pages). Key fields, all verified against real players (Saka, Lewis-Skelly):

| Field | Shape | Target |
|---|---|---|
| `id`, `name` | int, str | `Player.canonical_name`, seed key for `player_source_mapping` |
| `birthDate.utcTime` | ISO datetime str | `Player.date_of_birth` |
| `positionDescription.primaryPosition.label`/`.key` | `{"label": "Right Winger", "key": "rightwinger"}` | `Player.primary_position` |
| `positionDescription.positions[]` | list with `occurences`, `isMainPosition` | secondary positions (future `PlayerPosition` table, not MVP) |
| `playerInformation[]` | list of `{title, translationKey, value: {numberValue?, key?, fallback}}` | keyed by `translationKey`: `height_sentencecase` (cm), `shirt`, `age_sentencecase`, `preferred_foot` (`value.key`: "left"/"right"/"both"), `country_sentencecase` (+ `countryCode` ISO3), `transfer_value` (current market value, EUR, `numberValue`), `contract_end` (date) |
| `statSeasons[]` | index only — `{seasonName, tournaments: [{name, tournamentId, entryId, hasDeepStats}]}` | used to resolve the `entryId` needed for the (fixed) `player_stats` fetch — not itself a stats source |
| `mainLeague` | `{leagueId, leagueName, season, stats: [{title, localizedTitleId, value}]}` | current-season-only summary (goals/assists/started/matches/minutes_played/rating/yellow_cards/red_cards) — stopgap only, see blocker above |
| `traits.items[]` | `{key, title, value (0-1)}` e.g. `chances_created: 0.9` | new `PlayerAttributeProfile`-style table (see schema additions) |
| `marketValues.values[]` | `{date, value, currency, lowerBound, upperBound, source: "scisports", teamId, teamName}` | new `PlayerMarketValue` time-series table |
| `careerHistory`, `trophies`, `internationalDuty`, `injuryInformation`, `nextMatch`, `recentMatches` | present, rich | out of scope for MVP canonical schema; keep in raw model as passthrough fields, revisit for Pipeline B/F (analytics) later |

### `player_stats` (572 files — **currently duplicate of `player`**, see blocker)

Once fixed per above, will be the `https://www.fotmob.com/api/data/playerStats?playerId={id}&seasonId={entryId}` payload: `sectionOrder`, `shotmap` (match-by-match shot events with x/y/xG — future match-events table, not MVP `PlayerStats`), `statsSection.items[].items[]` (the season aggregate: `{localizedTitleId, title, statValue, per90, percentileRank, percentileRankPer90}` per metric — this is the real `PlayerStats` source), `topStatCard` (a curated subset of the same), `heatmap`, `keeperShotmap` (goalkeeper-specific, null for outfield players).

## Schema additions needed (`db/models.py`)

Two new tables, beyond what exists today (`Country, Competition, Season, Club, Player, PlayerStats, PlayerClubHistory, PlayerCompetitionHistory, PlayerSourceMapping, ScrapeLedger, EntityResolutionReview, FailedFetch`):

```python
class PlayerMarketValue(Base):
    __tablename__ = "player_market_value"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    source: Mapped[str] = mapped_column(String(32))
    as_of_date: Mapped[dt.date]
    value_eur: Mapped[int]
    lower_bound_eur: Mapped[int | None]
    upper_bound_eur: Mapped[int | None]
    valuation_source: Mapped[str]   # e.g. "scisports" -- FotMob's own upstream provider, not FotMob itself
    club_id_at_valuation: Mapped[int | None] = mapped_column(ForeignKey("club.id"))
    __table_args__ = (UniqueConstraint("player_id", "source", "as_of_date"),)

class PlayerAttributeProfile(Base):
    __tablename__ = "player_attribute_profile"
    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("player.id"))
    source: Mapped[str] = mapped_column(String(32))
    comparison_group_key: Mapped[str]   # e.g. "stats_comparison_att_mid_wingers"
    attributes: Mapped[dict] = mapped_column(JSONB)   # {"chances_created": 0.9, "aerials_won": 0.68, ...}
    computed_at: Mapped[dt.datetime]
    __table_args__ = (UniqueConstraint("player_id", "source", "comparison_group_key"),)
```

Both keyed loosely by `source` for consistency with the rest of the schema, even though only `"fotmob"` exists today.

## Module layout

```
etl/
├── models_raw/fotmob.py       # loose Pydantic models mirroring FotMob's shape, one per entity_type
├── parsers/fotmob_parser.py   # raw HTML/JSON -> models_raw instances
├── normalizers/
│   ├── club.py                # -> Country, Club
│   ├── player.py               # -> Player, PlayerMarketValue, PlayerAttributeProfile
│   └── player_stats.py         # -> PlayerStats, PlayerClubHistory, PlayerCompetitionHistory
├── entity_resolution/
│   └── seed.py                 # single-source now: each fotmob player_id seeds exactly one
│                                # Player row + one player_source_mapping row, match_method="seed".
│                                # No fuzzy matching needed with one source -- matcher.py's
│                                # scoring design from the original plan is dormant until/unless
│                                # a second source is reintroduced.
└── load/
    ├── db.py                   # session/engine (reuse db/session.py)
    └── loaders.py               # upsert helpers, ON CONFLICT DO UPDATE on natural keys
```

### `etl/models_raw/fotmob.py`

One Pydantic model per entity_type, fields typed loosely (dates as raw strings, not yet coerced), each carrying `raw_artifact_path: str` back to the source file:

- `FotMobCompetitionRaw` — `allAvailableSeasons`, standings rows (id, name, pageUrl, played/wins/draws/losses/pts)
- `FotMobClubRaw` — `id, name, country_code, squad_groups: list[{title, members}]`
- `FotMobPlayerRaw` — `id, name, birth_date_utc, primary_position_label, primary_position_key, player_information: list[RawInfoItem], stat_seasons: list[...], traits, market_values` (passthrough fields for career_history/trophies/international_duty kept as raw `dict` — not modeled field-by-field yet)
- `FotMobPlayerStatsRaw` — `player_id, tournament_id, season_entry_id, stats_section_items: list[{localized_title_id, stat_value, per90, percentile_rank}]`

### `etl/parsers/fotmob_parser.py`

Functions: `parse_competition(html) -> FotMobCompetitionRaw`, `parse_club(html, club_id) -> FotMobClubRaw` (reads the precise `fallback["team-{club_id}"]` path, not a generic search), `parse_player(html) -> FotMobPlayerRaw`, `parse_player_stats(json_bytes) -> FotMobPlayerStatsRaw`. All reuse `scraper.adapters._json_utils.extract_next_data` for the two HTML-based ones; `player_stats` (once fixed) is already-JSON, just `json.loads`.

### Normalizers

`normalizers/player.py::normalize_player(raw: FotMobPlayerRaw) -> Player` — maps `playerInformation` by `translationKey` into named columns (height, preferred_foot, nationality via `countryCode`, market value snapshot), extracts `primary_position` from `positionDescription`. Also emits `PlayerMarketValue` rows (one per `marketValues.values[]` entry) and a `PlayerAttributeProfile` row (one per `traits` block — currently one comparison group per player, e.g. attacking-mid/winger comparison).

`normalizers/player_stats.py::normalize_player_stats(raw, player_id, competition_id, season_id, club_id) -> PlayerStats` — promotes `goals`, `assists`, `minutes_played` (from `mainLeague.stats` as fallback, or `statsSection` once the fetch is fixed) to named columns; everything else from `statsSection.items[].items[]` goes into `PlayerStats.stats` JSONB keyed by `localized_title_id` (e.g. `{"expected_goals": {"value": 7.55, "per90": 0.305, "percentile_rank": 88.5}, ...}`).

`normalizers/club.py::normalize_club(raw) -> Club` — maps `country_code` (ISO3) to a `Country` row (create-if-missing), `name`.

### Entity resolution (single-source, simplified)

With Sofascore removed, `player_source_mapping` no longer needs the scoring/threshold machinery from the original two-source plan (name/DOB/nationality/club fuzzy matching). Each FotMob `player.id` becomes exactly one canonical `Player` row and one `player_source_mapping` row with `match_method="seed"`, `match_confidence=NULL`, `source="fotmob"`. `etl/entity_resolution/matcher.py`'s design stays documented but unimplemented/dormant — revisit only if a second source is reintroduced later.

### Load (`etl/load/loaders.py`)

Upsert functions per canonical table, `INSERT ... ON CONFLICT (natural key) DO UPDATE`, using SQLAlchemy Core or ORM `session.merge`-style patterns. Natural keys: `Player` on `(canonical_name, date_of_birth)` is not reliable enough alone (name collisions) — since we're single-source now, actually key off `player_source_mapping (source='fotmob', source_player_id)` first to find-or-create the canonical `Player`, rather than any name/DOB heuristic. `PlayerStats` on `(player_id, source, competition_id, season_id, club_id)` (already the DB unique constraint). `PlayerMarketValue` on `(player_id, source, as_of_date)`. `PlayerAttributeProfile` on `(player_id, source, comparison_group_key)`.

### CLI (`etl/cli.py`)

```
python -m etl run --source fotmob --competition epl --season 2025-2026 [--stage parse|normalize|load|all]
```

Reads only `data/raw/fotmob/`, never the network. `--stage` mirrors the scraper CLI's pattern for resumability.

## Validation rules

- `date_of_birth`: must parse, age-at-scrape-time in `[14, 45]` (per ProjectPlan.md's evaluation-dataset guidance on sanity checks) — reject to `data/processed/_rejects/` otherwise, don't drop silently.
- `PlayerStats.minutes_played <= appearances * 120` (per original plan's sanity check) — will need `appearances` promoted from `statsSection` (`localized_title_id == "matches_uppercase"` per `topStatCard`/`mainLeague` naming seen above) once the `player_stats` fetch is fixed.
- Every `Club` must resolve to a `Country` row (ISO3 `country_code` from the club raw file) before insert — `Country` table needs an ISO3-keyed lookup/seed step, not just the country *name* strings already used for `Competition.country`.

## Testing / fixtures

Real files already on disk are the fixtures — no need to fabricate sample data:
- `tests/unit/fixtures/fotmob_competition.html` ← copy from `data/raw/fotmob/competition/47/*.html`
- `tests/unit/fixtures/fotmob_club.html` ← `data/raw/fotmob/club/9825/*.html` (Arsenal)
- `tests/unit/fixtures/fotmob_player.html` ← `data/raw/fotmob/player/961995/*.html` (Saka)
- `tests/unit/fixtures/fotmob_player_stats.json` ← **capture fresh once the scraper fix lands**; do not copy the current (duplicate/wrong-season) `player_stats` files as fixtures, they'd pin the bug.

## Sequencing

1. Fix `scraper/adapters/fotmob.py::fetch_player_stats` (the entryId-based endpoint) — blocks correct `PlayerStats` normalization.
2. Re-run `player_stats`-stage-only crawl for all 572 players against the fix (profile/`player` data doesn't need re-fetching).
3. Add `PlayerMarketValue`/`PlayerAttributeProfile` to `db/models.py`, generate Alembic migration.
4. Build `etl/models_raw/fotmob.py` + `etl/parsers/fotmob_parser.py`, unit-tested against the real fixtures above.
5. Build normalizers + loaders, wire `etl/cli.py`.
6. Run end-to-end against the full 20-club/572-player raw set already collected; spot-check known players (Saka, Haaland-equivalent) for correctness per the original plan's verification approach.

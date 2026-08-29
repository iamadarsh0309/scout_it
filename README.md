# scout_it

Data collection tool for an AI football scouting platform. This repo currently implements
**only Pipeline A** of the full platform design — a standalone crawler that discovers
competitions/clubs/players via browser automation (Playwright) against FotMob, and lands
normalized player data in PostgreSQL. It does not yet do requirement parsing, ML ranking, RAG,
or report generation.

FotMob is currently the sole data source. The adapter interface and generic per-source
plumbing (`config/settings.py`'s `sources` mapping, `config/coverage.yaml`'s per-source IDs,
the `player_source_mapping` table, etc.) remain source-agnostic in case a second source is
added later, but there is currently nothing else to cross-match against.

See [`ProjectPlan.md`](./ProjectPlan.md) for the full platform vision (Pipelines A–L: data
extraction, feature engineering, requirement understanding, retrieval, ranking, RAG, scouting
reports, evaluation, feedback loop). This README covers only what's actually built.

## What this is, concretely

FotMob has no official public API. It exposes an internal JSON API used by its own frontend,
and this tool drives a real headless browser (Playwright) against the actual site —
navigating pages and reading the server-rendered HTML/embedded JSON — rather than hitting that
internal API host directly with a bare HTTP client. Raw responses are stored untouched on
disk; a separate (not-yet-built) ETL stage will turn them into rows in Postgres.

## Architecture

### Pipeline A data flow

```mermaid
flowchart LR
    A[Data Source<br/>FotMob] --> B[Source Adapter<br/>scraper/adapters/]
    B --> C[Raw Data Store<br/>data/raw/ + scrape_ledger]
    C -.not yet built.-> D[Parser<br/>etl/parsers/]
    D -.not yet built.-> E[Validation]
    E -.not yet built.-> F[Normalization<br/>etl/normalizers/]
    F -.not yet built.-> G[Entity Resolution<br/>etl/entity_resolution/]
    G -.not yet built.-> H[(PostgreSQL<br/>db/models.py)]
```

Everything left of the raw store is implemented and runnable today
(`python -m scraper crawl ...`). Everything from the parser onward is scaffolded
(empty `etl/` subpackages) but has no logic yet.

### Discovery hierarchy

```mermaid
flowchart TD
    Countries --> Competitions
    Competitions --> Seasons
    Seasons --> Clubs
    Clubs --> Squads
    Squads --> Players
    Players --> Stats[Player profiles / statistics]
```

In the current implementation, `Countries -> Competitions -> Seasons` is not crawled live —
it's pinned in `config/coverage.yaml` per source (see `competitions_from_coverage` /
`seasons_from_coverage` in `scraper/base.py`). Live browser discovery starts at
`Clubs -> Squads -> Players -> Stats`, driven by `scraper/cli.py`'s `--stage` flag.

### Module relationships

```mermaid
flowchart TB
    subgraph config["config/ (implemented)"]
        settings[settings.py<br/>DB URL, rate limits, ER thresholds]
        coverage[coverage.yaml<br/>competitions/seasons per source]
        aliases[name_aliases.yaml<br/>manual ER overrides]
    end

    subgraph scraper["scraper/ (implemented)"]
        cli[cli.py<br/>crawl orchestrator]
        base[base.py<br/>BaseSourceAdapter + Discovered* dataclasses]
        browser[browser.py<br/>BrowserSessionManager]
        ratelimit[rate_limit.py]
        retry[retry.py]
        rawstore[raw_store.py<br/>LocalFileRawStore]
        fingerprint[fingerprint.py<br/>content-hash skip logic]
        adapters["adapters/fotmob.py"]
    end

    subgraph db["db/ (implemented)"]
        models[models.py<br/>SQLAlchemy ORM schema]
        session[session.py]
        migrations["migrations/ (Alembic, no versions yet)"]
    end

    subgraph etl["etl/ (scaffolded, no logic)"]
        modelsraw[models_raw/]
        parsers[parsers/]
        normalizers[normalizers/]
        entityres[entity_resolution/]
        load[load/]
    end

    cli --> base
    cli --> browser
    cli --> rawstore
    cli --> fingerprint
    cli --> config
    cli --> session
    adapters --> base
    adapters --> browser
    adapters --> ratelimit
    adapters --> retry
    adapters --> rawstore
    cli -.will read from.-> rawstore
    etl -.future: writes to.-> models
```

## Current implementation status

**Done:**

- `scraper/` package — `BaseSourceAdapter` interface, `BrowserSessionManager` (Playwright
  context lifecycle, per-source concurrency), rate limiting, retry/backoff with block
  detection, filesystem raw storage (`data/raw/{source}/{entity_type}/{id}/{timestamp}.*`),
  content-hash fingerprinting for skip-if-unchanged, and a working `FotMobAdapter`
  implementation.
- `scraper/cli.py` — the `python -m scraper crawl` orchestrator with staged execution,
  freshness-based skipping, and failed-fetch logging to Postgres.
- `db/models.py` — the full SQLAlchemy schema: `Country`, `Competition`, `Season`, `Club`,
  `Player`, `PlayerStats`, `PlayerClubHistory`, `PlayerCompetitionHistory`,
  `PlayerSourceMapping`, `ScrapeLedger`, `EntityResolutionReview`, `FailedFetch`.
- `db/migrations/` — Alembic wired to `db/models.py` via `db/migrations/env.py`.
- `config/` — `settings.py` (pydantic-settings: DB URL, per-source rate limits/concurrency,
  entity-resolution thresholds), `coverage.yaml` (competition/season IDs per source),
  `name_aliases.yaml` (manual entity-resolution overrides, currently empty).

**Not yet done:**

- `etl/` — `models_raw`, `parsers`, `normalizers`, `entity_resolution`, `load` are all empty
  package skeletons. No parsing of the raw HTML/JSON, no normalization into the canonical
  schema, no entity-resolution scoring/linking logic, no loader that writes into Postgres.
  This means a crawl today populates `data/raw/` and the `scrape_ledger` table, but **no
  `Player`/`PlayerStats` rows exist in Postgres yet** — the ORM schema is defined and
  migratable but nothing currently writes canonical data into it.
- `tests/` — `unit/` and `integration/` are empty package skeletons, no test cases.
- `scripts/` — referenced in the design plan (e.g. an end-to-end verification script) but the
  directory does not exist in the repo yet.
- No Alembic migration versions have been generated yet (`db/migrations/versions/` is empty).

## Setup

Python is pinned to 3.12 via `.python-version` (Playwright and the Postgres driver wheels can
lag support for newer interpreters).

```bash
uv sync
uv run playwright install chromium
```

### Database

Point the tool at Postgres via the `SCOUT_IT_DATABASE_URL` environment variable (or a `.env`
file), read by `config/settings.py`:

```bash
export SCOUT_IT_DATABASE_URL="postgresql+psycopg://scout_it:scout_it@localhost:5432/scout_it"
```

If unset, it defaults to that same local `scout_it`/`scout_it`@`localhost:5432/scout_it`.

No migration versions exist yet, so the first real step is generating one from the current
models, then applying it:

```bash
uv run alembic revision --autogenerate -m "initial schema"
uv run alembic upgrade head
```

## Usage

Crawl a competition/season using a source adapter:

```bash
uv run python -m scraper crawl --source fotmob --competition epl --stage discover-clubs
```

Flags (see `scraper/cli.py`):

- `--source` — `fotmob` or `all` (currently equivalent, since FotMob is the only source
  implemented).
- `--competition` — a slug from `config/coverage.yaml` (see below).
- `--season` — a season label (e.g. `2025-2026`); defaults to whatever season(s) are marked
  `active: true` for that competition.
- `--stage` — one of `discover-clubs`, `discover-squads`, `fetch-players`, `fetch-stats`,
  `all`. Each stage builds on the previous one within a single run; use this to resume a
  partial crawl or to inspect discovery output before fetching full player data.
- `--force` — bypass the freshness/ledger skip (by default, players fetched within
  `player_freshness_days`, default 7, are skipped).

Coverage is config-driven: `config/coverage.yaml` maps competition slugs (`epl`, `laliga`,
`brasileirao`) to per-source competition/season IDs. Only `epl` (Premier League) has
`active: true` today; `laliga` and `brasileirao` are present but inactive placeholders —
flipping `active: true` and filling in season IDs is a config change, not a code change.

FotMob's rate limit defaults to a 3-6s delay between requests with concurrency 2 (see
`config/settings.py`). Expect a full-EPL crawl to take a while — treat crawls as resumable
background jobs, not quick scripts.

## Legal / risk note

FotMob's Terms of Service explicitly prohibit automated scraping/data mining. It offers no
official public API for this data. This project accepts that risk deliberately, scoped to
personal/educational use: low volume (one competition for MVP), respecting the rate limits
configured per source, and not redistributing raw scraped artifacts. This is not a compliance
claim — it's an explicit, accepted risk, not a resolved one.

## What's next

The immediate next milestone is implementing `etl/`: parsing the raw HTML/JSON artifacts
already being collected in `data/raw/` into typed raw models, validating and normalizing them
into the canonical schema in `db/models.py`, and loading the result into Postgres. The
`player_source_mapping` table and entity-resolution scaffolding (`etl/entity_resolution/`,
`auto_link_threshold`/`review_queue_threshold` in `config/settings.py`) exist to link the same
player across multiple sources, but with only FotMob implemented there is currently nothing to
cross-match — that logic becomes relevant again if a second source is added. Until the ETL
stage exists, a crawl only produces raw files and a scrape ledger — no queryable player data.

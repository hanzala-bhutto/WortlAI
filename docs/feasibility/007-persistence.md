# Feasibility: 007 - SQLite persistence (sessions, errors, hours)

- **Issue**: #6 · **Phase**: 1 · **Date**: 2026-07-25 (implemented 2026-07-26) · **Author**: Claude (reviewed by Hanzala)

## Goal
SQLAlchemy models for sessions, error_logs, immersion_logs (source: app|call|mission) + POST /log-hours for manual call/mission logging.

## Approach options
1. **SQLAlchemy + Alembic from day one (chosen)** - schema will evolve every phase; migrations from the start beat "recreate the DB" pain later.
2. Raw SQL / no migrations - simpler today, guaranteed regret in Phase 2 when FSRS + graph tables land.

## Schema
Models in `app/learner/models.py` are the source of truth; the Alembic migration in `alembic/versions/` is the DDL generated from them. Three tables, kept to exactly what this issue consumes.

**sessions** - one row per conversation.

| Field | Reason |
| --- | --- |
| `id` | PK; identity that errors and hours reference |
| `started_at` | When it began; orders sessions, anchors the day the time counts toward |
| `ended_at` | When it finished; NULL means still open or abandoned, which is real state |
| `scenario` | Which roleplay (nullable for free talk); drives curriculum rotation + debrief |

`duration_seconds` is a derived Python property (`ended_at - started_at`), not a stored column, so it cannot drift from the timestamps. Relations: `errors` and `immersion` (1-to-many, cascade delete).

**error_logs** - one row per mistake caught.

| Field | Reason |
| --- | --- |
| `id` | PK |
| `session_id` | FK to the conversation; ON DELETE CASCADE |
| `created_at` | When caught; powers the `since` filter and trends without a join |
| `error_type` | Dotted taxonomy (e.g. `grammar.case.dative`); indexed, trends group by it |
| `severity` | `critical`/`minor`; enforces fluency-before-accuracy staging |
| `utterance` | What was said (the wrong form) |
| `correction` | The fix; what an FSRS card or debrief item is built from |

Relation: `session` (many-to-one).

**immersion_logs** - one row per block of German time.

| Field | Reason |
| --- | --- |
| `id` | PK |
| `created_at` | The timestamp `hours_per_day` groups by; core |
| `source` | `app`/`call`/`mission`; attributes the hours |
| `minutes` | The quantity |
| `note` | Optional context ("Bäckerei mission") so a manual entry stays reviewable |
| `session_id` | Nullable FK; app rows link to their session (cascade), manual rows stay NULL |

Relation: `session` (many-to-one, optional). App hours enter the metric via `record_session_immersion`, so sessions and manual logs sum in one table with no double counting.

## Fields deliberately excluded
Kept to this issue's scope; each is a one-line migration when the issue that writes it lands, which is the whole point of Alembic-from-day-one.

- `sessions.cefr_level` - the Tutor pins the level; added in #4 when something writes it.
- `error_logs.explanation` (and likely a confidence score) - the Corrector owns the shape of what it writes; added in #5.
- `sessions.duration_seconds` as a column - redundant with the two timestamps; derived instead.

## Decisions
- **Two SQLite files**: app DB (`wortlai.db`) and the LangGraph checkpointer (`checkpoints.db`, later) stay separate so the two writers never share a connection.
- **Pragmas per connection**: `journal_mode=WAL` (reader + writer concurrency, so /progress can read mid-session) and `foreign_keys=ON` (SQLite defaults it off; without it the FK and cascade are decorative).
- **Named constraints**: a `naming_convention` on the metadata so SQLite batch-mode migrations can alter/drop FKs by name in later phases. Free now, painful to retrofit after a migration ships.
- **Schema on first run**: `alembic upgrade head` runs in a FastAPI lifespan at startup, so a fresh clone needs no manual step. The test client, instantiated without a `with` block, never triggers it, keeping the suite hermetic.
- **Sync SQLAlchemy, not aiosqlite**: SQLite has one globally-locked writer, and aiosqlite runs the blocking driver in a background thread anyway, so async buys no concurrency here for a single user. Alembic and py-fsrs are sync; async would add AsyncSession plumbing and a second engine for nothing. `db.py` is the single seam if we ever move to Postgres.

## Risks & unknowns
- Two SQLite writers (app DB + LangGraph checkpointer) → separate files, WAL mode; no shared-writer contention.
- Datetimes are stored naive-UTC (SQLite drops tzinfo); `duration_seconds` and `mark_ended` coerce defensively. Dresden-local day boundaries for hours belong to the /progress dashboard (#27), not here.

## Free-tier impact
None.

## Effort estimate
S (<2h).

## Verdict
**GO**.

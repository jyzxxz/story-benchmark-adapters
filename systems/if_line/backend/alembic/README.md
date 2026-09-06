# Database migrations

The API no longer creates or alters tables at import/startup. Run migrations as
a separate deployment step from the `backend` directory:

```bash
venv/bin/python -m alembic upgrade head
```

For a **new empty database**, `upgrade head` creates the legacy schema and then
advances through the additive migration chain.

For an **existing legacy database**, first back up both the database and media,
run a schema/data preflight, then mark the existing schema baseline before
upgrading:

```bash
venv/bin/python scripts/preflight_legacy_database.py
venv/bin/python -m alembic stamp 0001_current_schema
venv/bin/python -m alembic upgrade head
```

Never stamp an empty database. Never stamp a legacy database until preflight
has verified that the expected legacy tables exist. The production database is
not migrated automatically by application startup.

The conditional `0002_runtime_foundation` legacy bridge must run in online
mode. Offline SQL generation intentionally treats the database as new because
`0001_current_schema` already contains those columns; do not use an offline SQL
bundle to upgrade a stamped legacy database.

## Model-backed v2 migration boundary

Revisions `0003` through `0007` create a fixed, explicitly ordered list of v2
tables from SQLAlchemy table metadata. This keeps constraints, indexes, JSON,
timezone-aware timestamps and PostgreSQL `ON DELETE` behavior identical to the
current v2 persistence models without duplicating roughly a thousand lines of
table declarations.

This mechanism has an important limitation: changing a listed table in
`app/models_v2.py` would also change what a fresh run of an old migration emits.
Therefore:

1. existing v2 table definitions must not be edited in place after release;
2. every later schema change must use a new Alembic revision; and
3. `test_runtime_foundation.py` locks the emitted SQLite schema with a
   hard-coded table/column/constraint/index/foreign-key fingerprint.

The fingerprint must never be updated merely to make CI pass. A mismatch means
the model changed without a new migration and must be resolved by freezing the
old definition or adding a new revision.

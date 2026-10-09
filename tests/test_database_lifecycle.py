from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime

import pytest
from sqlalchemy import Column, Integer, String, Table, event, text
from sqlmodel import SQLModel, select

from buyeeye import db, maintenance
from buyeeye.db import Listing, get_session, init_db
from buyeeye.runtime_state import mark_listing_seen


@pytest.fixture(autouse=True)
def isolated_engine_ownership():
    db._dispose_database_engines()
    yield
    db._dispose_database_engines()


def test_repeated_cards_share_engine_without_schema_queries(tmp_path, monkeypatch):
    path = tmp_path / "cards.db"
    created = []
    statements = []
    original = db.create_engine

    def create(*args, **kwargs):
        engine = original(*args, **kwargs)
        created.append(engine)
        event.listen(engine, "before_cursor_execute", lambda _c, _cu, sql, *_: statements.append(sql))
        return engine

    monkeypatch.setattr(db, "create_engine", create)
    init_db(path)
    statements.clear()
    for number in range(100):
        mark_listing_seen("buyee", str(number), "signature", "fixture", path)
    assert len(created) == 1
    assert created[0].pool.checkedout() == 0
    assert created[0].pool.checkedin() == 1
    assert not any(sql.upper().startswith("PRAGMA") for sql in statements)
    assert not any("sqlite_master" in sql for sql in statements)
    with get_session(path) as session:
        assert session.execute(text("SELECT COUNT(*) FROM seenlisting")).scalar_one() == 100


def test_canonical_paths_share_engine_and_databases_stay_separate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "first.db"
    with get_session("first.db") as session:
        session.add(Listing(source="fixture", source_listing_id="first"))
        session.commit()
        engine = session.get_bind()
    with get_session(path) as session:
        assert session.get_bind() is engine
        assert session.exec(select(Listing)).one().source_listing_id == "first"
    with get_session(tmp_path / "other.db") as session:
        assert session.get_bind() is not engine
        assert session.exec(select(Listing)).all() == []


def test_closed_session_rolls_back_without_poisoning_shared_connection(tmp_path):
    path = tmp_path / "rollback.db"
    with get_session(path) as session:
        session.add(Listing(source="fixture", source_listing_id="uncommitted"))
        session.flush()
    with get_session(path) as session:
        assert session.exec(select(Listing)).all() == []
        session.add(Listing(source="fixture", source_listing_id="committed"))
        session.commit()
    with get_session(path) as session:
        assert session.exec(select(Listing)).one().source_listing_id == "committed"


def test_schema_initialization_retains_additive_legacy_migration(tmp_path):
    path = tmp_path / "legacy.db"
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("CREATE TABLE listing (id INTEGER PRIMARY KEY, source TEXT, source_listing_id TEXT)")
        connection.execute("INSERT INTO listing VALUES (1, 'buyee', 'preserved')")
    with get_session(path) as session:
        row = session.get(Listing, 1)
        assert row.source_listing_id == "preserved"
        assert row.image_url is None
        row.image_url = "https://example.test/fixture.jpg"
        session.commit()
    init_db(path)
    with get_session(path) as session:
        assert session.get(Listing, 1).image_url == "https://example.test/fixture.jpg"


def test_late_registered_tables_and_columns_invalidate_schema_marker(tmp_path):
    path = tmp_path / "late.db"
    init_db(path)
    table = Table("late_resource_probe", SQLModel.metadata, Column("id", Integer, primary_key=True))
    try:
        init_db(path)
        with get_session(path) as session:
            session.execute(text("INSERT INTO late_resource_probe (id) VALUES (1)"))
            session.commit()
        table.append_column(Column("added_later", String))
        with get_session(path) as session:
            assert session.execute(text("SELECT id, added_later FROM late_resource_probe")).one() == (1, None)
    finally:
        SQLModel.metadata.remove(table)


def test_replaced_database_gets_new_engine_and_schema(tmp_path):
    path = tmp_path / "replace.db"
    with get_session(path) as session:
        old_engine = session.get_bind()
        session.add(Listing(source="fixture", source_listing_id="old-inode"))
        session.commit()
    replacement = tmp_path / "replacement.db"
    with closing(sqlite3.connect(replacement)) as connection, connection:
        connection.execute("CREATE TABLE owner_marker(value TEXT)")
        connection.execute("INSERT INTO owner_marker VALUES ('replacement')")
    replacement.replace(path)
    with get_session(path) as session:
        assert session.get_bind() is not old_engine
        assert session.exec(select(Listing)).all() == []
        assert session.execute(text("SELECT value FROM owner_marker")).scalar_one() == "replacement"
    assert old_engine.pool.checkedin() == 0


def test_engine_cache_is_bounded_and_disposes_evicted_pool(tmp_path):
    with get_session(tmp_path / "first.db") as session:
        old_engine = session.get_bind()
        session.execute(text("SELECT 1"))
    assert old_engine.pool.checkedin() == 1
    for number in range(db._MAX_DATABASE_ENGINES):
        init_db(tmp_path / f"other-{number}.db")
    assert len(db._database_engines) == db._MAX_DATABASE_ENGINES
    assert old_engine.pool.checkedin() == 0


def test_shutdown_and_restart_preserve_rows_and_dedupe(tmp_path):
    path = tmp_path / "restart.db"
    first = mark_listing_seen("buyee", "preserved", "unchanged", "fixture", path)
    assert first.lifetime_first_seen
    with get_session(path) as session:
        engine = session.get_bind()
        session.add(Listing(source="buyee", source_listing_id="preserved"))
        session.commit()
    db._dispose_database_engines()
    assert engine.pool.checkedin() == 0
    revisit = mark_listing_seen("buyee", "preserved", "unchanged", "fixture", path)
    assert not revisit.lifetime_first_seen
    assert revisit.is_duplicate
    with get_session(path) as session:
        assert session.exec(select(Listing)).one().source_listing_id == "preserved"


def test_failed_initialization_can_retry(tmp_path, monkeypatch):
    original = db._migrate_schema
    attempts = 0

    def migrate(engine):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("synthetic migration interruption")
        return original(engine)

    monkeypatch.setattr(db, "_migrate_schema", migrate)
    path = tmp_path / "retry.db"
    with pytest.raises(RuntimeError, match="interruption"):
        init_db(path)
    with get_session(path) as session:
        assert session.exec(select(Listing)).all() == []
    assert attempts == 2


def test_fork_discard_never_closes_inherited_parent_connection(tmp_path, monkeypatch):
    with get_session(tmp_path / "fork.db") as session:
        engine = session.get_bind()
    disposals = []
    original = engine.dispose
    monkeypatch.setattr(engine, "dispose", lambda **kwargs: disposals.append(kwargs))
    db._discard_inherited_database_engines()
    assert disposals == [{"close": False}]
    assert not db._database_engines
    original()


def test_native_maintenance_connections_close_on_success_and_error(tmp_path, monkeypatch):
    path = tmp_path / "maintenance.db"
    connections = []
    original = maintenance._connect

    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(maintenance, "_connect", connect)
    maintenance._prepare(path)
    assert maintenance.optional_diagnostics_allowed(path)
    empty = tmp_path / "no-schema.db"
    sqlite3.connect(empty).close()
    # Missing table is a handled read failure, but still must close its connection.
    assert maintenance.optional_diagnostics_allowed(empty)
    maintenance.storage_summary(path)
    maintenance.run_maintenance_slice(path, force=True)
    monkeypatch.setattr("buyeeye.discord_alerts.send_status_event", lambda *_: (True, 204, 0))
    maintenance._warn_storage(path, datetime.now(UTC), 1, maintenance.DEFAULT_POLICY)
    assert len(connections) >= 7
    for connection in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            connection.execute("SELECT 1")

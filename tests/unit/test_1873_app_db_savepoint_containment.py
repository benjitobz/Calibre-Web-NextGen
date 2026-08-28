# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Regression coverage for #1873's uncontained app.db SAVEPOINT."""

from __future__ import annotations

import sqlite3
from types import SimpleNamespace

import pytest

from cps import ub
from cps.services.annotation_sync import (
    dispatch_existing_annotation_sync,
    register_handler,
    reset_registry_for_testing,
    set_remote_enqueue,
)
from cps.services.annotation_sync.base import AnnotationSyncTargetHandler, SyncResult


class _StubHandler(AnnotationSyncTargetHandler):
    target_name = "stub"

    def is_enabled(self, user):
        return True

    def push(self, annotation, book, user, payload=None):
        return SyncResult(status="synced", target_record_id="remote-1873")

    def delete(self, sync_target, user):
        return SyncResult(status="tombstone")


@pytest.fixture(autouse=True)
def _reset_annotation_sync_registry():
    reset_registry_for_testing()
    set_remote_enqueue(None)
    yield
    reset_registry_for_testing()
    set_remote_enqueue(None)


@pytest.fixture
def file_backed_app_db(tmp_path):
    """Initialize the real app.db engine and restore the module globals after."""
    previous_session = ub.session
    previous_path = ub.app_DB_path
    db_path = tmp_path / "app.db"

    ub.init_db(str(db_path))
    test_session = ub.session
    try:
        yield db_path, test_session
    finally:
        engine = test_session.get_bind()
        test_session.close()
        engine.dispose()
        ub.session = previous_session
        ub.app_DB_path = previous_path


@pytest.mark.unit
def test_existing_annotation_sync_savepoint_rolls_back_with_failed_commit(
    file_backed_app_db, monkeypatch,
):
    """The web-reader/KOReader create arm must not commit at SAVEPOINT release.

    ``dispatch_annotation_sync`` (the Kobo PATCH entry point) flushes an
    Annotation before it creates the sync-target row. That DML happens to make
    sqlite3's legacy transaction mode contain its SAVEPOINT, so testing that
    caller passes with or without the fix. ``dispatch_existing_annotation_sync``
    reaches the same create arm after SELECTs only and is the discriminating
    real caller.

    Seed through a separate sqlite3 connection so this SQLAlchemy connection
    has emitted no DML before the dispatcher opens ``begin_nested()``. Then
    model a failed outer commit by rolling back, and verify durability through
    another independent connection rather than the session under test.
    """
    db_path, session = file_backed_app_db
    session.rollback()

    with sqlite3.connect(db_path) as seed:
        user_id = seed.execute(
            "SELECT id FROM user WHERE name = 'admin'"
        ).fetchone()[0]
        seed.execute(
            "INSERT INTO annotation "
            "(user_id, annotation_id, book_id, source, routing_revision, content_revision) "
            "VALUES (?, 'webreader-1873', 1873, 'webreader', 1, 1)",
            (user_id,),
        )

    user = session.query(ub.User).filter(ub.User.id == user_id).one()
    annotation = session.query(ub.Annotation).filter(
        ub.Annotation.annotation_id == "webreader-1873"
    ).one()
    register_handler(_StubHandler())

    commit_attempts = []

    def fail_outer_commit():
        commit_attempts.append(True)
        session.rollback()
        return False

    monkeypatch.setattr(ub, "session_commit", fail_outer_commit)

    dispatch_existing_annotation_sync(
        annotation,
        SimpleNamespace(id=1873, title="SAVEPOINT regression"),
        user,
    )

    assert commit_attempts == [True], "the test did not exercise the failed commit path"
    with sqlite3.connect(db_path) as observer:
        durable_targets = observer.execute(
            "SELECT target, status FROM annotation_sync_target "
            "WHERE annotation_id = ?",
            (annotation.id,),
        ).fetchall()

    assert durable_targets == [], (
        "the sync-target SAVEPOINT committed at RELEASE and survived the outer rollback"
    )


@pytest.mark.unit
def test_every_app_db_session_uses_explicit_begin_and_factory_keeps_timeout(
    file_backed_app_db,
):
    """The main, worker, and ad-hoc app.db constructors share WAL + the fix."""
    _db_path, main_session = file_backed_app_db
    thread_session = ub.init_db_thread()
    ad_hoc_session = ub.get_new_session_instance()
    sessions = {
        "init_db": main_session,
        "init_db_thread": thread_session,
        "get_new_session_instance": ad_hoc_session,
    }

    try:
        for constructor, session in sessions.items():
            connection = session.connection()
            driver_connection = connection.connection.driver_connection
            assert driver_connection.isolation_level is None, constructor
            assert connection.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "wal"
            session.rollback()

        # Startup migrations temporarily lower busy_timeout on the pooled main
        # connection for their own retry loop. Check a fresh factory connection
        # so this pins the connect_args=30 contract rather than that unrelated,
        # pre-existing migration policy.
        timeout_engine = ub._create_app_db_engine(_db_path)
        try:
            with timeout_engine.connect() as connection:
                assert connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one() == 30_000
        finally:
            timeout_engine.dispose()
    finally:
        thread_engine = thread_session.get_bind()
        thread_session.close()
        thread_engine.dispose()
        ad_hoc_engine = ad_hoc_session.get_bind()
        ad_hoc_session.remove()
        ad_hoc_engine.dispose()


@pytest.mark.unit
def test_wal_unavailable_suppresses_explicit_begin_warns_and_does_not_block_writer(
    tmp_path, monkeypatch,
):
    """A WAL-incapable engine degrades consistently and observably."""
    db_path = tmp_path / "wal-unavailable.db"
    with sqlite3.connect(db_path) as setup:
        setup.execute("CREATE TABLE probe (value TEXT NOT NULL)")
        setup.execute("INSERT INTO probe VALUES ('seed')")

    wal_requests = []

    def reject_wal(connection):
        wal_requests.append(connection)
        return "delete", None

    monkeypatch.setattr(ub, "_request_app_db_wal", reject_wal)
    warnings = []

    def capture_warning(message, *args):
        warnings.append(message % args)

    monkeypatch.setattr(ub.log, "warning", capture_warning)
    engine = ub._create_app_db_engine(db_path)

    try:
        with engine.connect() as reader:
            assert reader.exec_driver_sql("SELECT value FROM probe").scalar_one() == "seed"
            driver_connection = reader.connection.driver_connection
            assert driver_connection.isolation_level is not None
            assert driver_connection.in_transaction is False

            # Keep the reader checked out so this must create another DBAPI
            # connection. The WAL capability probe is engine-wide, not a
            # per-connection choice that could produce mixed semantics.
            with engine.connect() as sibling:
                sibling_driver = sibling.connection.driver_connection
                assert sibling_driver is not driver_connection
                assert sibling_driver.isolation_level is not None
                assert sibling.exec_driver_sql("SELECT count(*) FROM probe").scalar_one() == 1
                assert sibling_driver.in_transaction is False

            # In rollback-journal mode, a legacy SELECT releases its read lock
            # with the statement. An independent writer must not wait for this
            # SQLAlchemy Connection's bookkeeping transaction to be closed.
            with sqlite3.connect(db_path, timeout=0.25) as writer:
                writer.execute("INSERT INTO probe VALUES ('writer')")
    finally:
        engine.dispose()

    assert len(wal_requests) == 1
    assert len(warnings) == 1
    assert "WAL is unavailable" in warnings[0]
    assert "legacy sqlite3 transaction control" in warnings[0]
    assert "begin_nested() SAVEPOINTs opened before DML are not contained" in warnings[0]

    with sqlite3.connect(db_path) as observer:
        assert observer.execute("SELECT value FROM probe ORDER BY rowid").fetchall() == [
            ("seed",),
            ("writer",),
        ]


@pytest.mark.unit
def test_network_share_mode_skips_wal_uses_legacy_transactions_and_warns(
    tmp_path, monkeypatch,
):
    """NETWORK_SHARE_MODE applies to app.db even when its own path is local."""
    db_path = tmp_path / "network-share-mode.db"
    with sqlite3.connect(db_path) as setup:
        setup.execute("CREATE TABLE probe (value TEXT NOT NULL)")
        setup.execute("INSERT INTO probe VALUES ('seed')")

    monkeypatch.setenv("NETWORK_SHARE_MODE", "true")

    def unexpected_wal_request(_connection):
        raise AssertionError("NETWORK_SHARE_MODE must skip PRAGMA journal_mode=WAL")

    monkeypatch.setattr(ub, "_request_app_db_wal", unexpected_wal_request)
    warnings = []
    monkeypatch.setattr(
        ub.log,
        "warning",
        lambda message, *args: warnings.append(message % args),
    )
    engine = ub._create_app_db_engine(db_path)

    try:
        with engine.connect() as reader:
            driver_connection = reader.connection.driver_connection
            assert reader.exec_driver_sql("PRAGMA journal_mode").scalar_one() == "delete"
            assert driver_connection.isolation_level is not None
            assert reader.exec_driver_sql("SELECT value FROM probe").scalar_one() == "seed"
            assert driver_connection.in_transaction is False

            # A second DBAPI connection must inherit the engine-wide decision
            # without another warning or a deferred WAL negotiation.
            with engine.connect() as sibling:
                sibling_driver = sibling.connection.driver_connection
                assert sibling_driver is not driver_connection
                assert sibling_driver.isolation_level is not None
    finally:
        engine.dispose()

    assert len(warnings) == 1
    assert "NETWORK_SHARE_MODE=true" in warnings[0]
    assert "even when /config is on local disk" in warnings[0]
    assert "#1873 SAVEPOINT containment fix is unavailable" in warnings[0]

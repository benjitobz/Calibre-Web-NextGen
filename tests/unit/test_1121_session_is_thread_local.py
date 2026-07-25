# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#1121 — `calibre_db.session` must be per-thread, and currently is not.

`CalibreDB.session_factory` is a `scoped_session`, which hands each thread its
own Session. `init_session()` then does `self.session = self.session_factory()`,
storing that thread's Session on a **single shared attribute** of a
module-level singleton. Whichever thread called `init_session()` most recently
publishes its Session there, and the ~270 call sites that read
`calibre_db.session` all get it — so the thread-locality the wrapper exists to
provide is discarded by the assignment.

CWNG runs gevent WITHOUT `monkey.patch_all()` (see the AST guard in
`test_no_stdlib_futures_on_request_path`), so `WorkerThread` is a real OS
thread. Two genuinely concurrent users of one Session and one SQLite
connection.

OBSERVED, and what this file pins: a thread's session changes underneath it
once another thread calls `init_session()`. Proven below.

NOT OBSERVED, and deliberately not asserted here: that this sharing is what
produces the #1048 traceback (`sqlite3.ProgrammingError: Cannot operate on a
closed database`, raised from `sqlalchemy/orm/loading.py` `chunks` 161 ms after
`Manual scan queued`, with `cps/tasks/duplicate_scan.py:325` calling
`calibre_db.session.close()` at task teardown). Two attempts to reproduce that
link failed: a Session survives `close()` and simply re-acquires a connection,
even under NullPool, so the sequential close-then-use path is harmless. The
reporter's traceback is a cursor being drained, so reproducing it needs a
genuine mid-fetch interleave — a worker closing while a request thread is
partway through a large result set. Until someone builds that, the crash
mechanism stays a hypothesis and should be described as one.

The sharing is a real defect on its own merits regardless: 270 call sites read
a session that any thread can replace.

This test asserts the CORRECT behaviour, so it is RED until #1121 is fixed and
is quarantined in `tests/quarantine.py` with that issue reference. It goes green
on its own once `session` resolves per-thread rather than being assigned to a
shared attribute.
"""

from __future__ import annotations

import tempfile
import threading

import pytest

pytestmark = pytest.mark.unit


def _isolated_calibredb():
    """A CalibreDB wired to a throwaway engine, with the real session plumbing."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import scoped_session, sessionmaker

    from cps.db import CalibreDB

    tmp = tempfile.mkdtemp()
    engine = create_engine(f"sqlite:///{tmp}/probe.db", future=True)
    CalibreDB.engine = engine
    CalibreDB.session_factory = scoped_session(
        sessionmaker(bind=engine, autocommit=False, autoflush=True, future=True))
    return CalibreDB(), CalibreDB


def test_scoped_session_really_does_hand_each_thread_its_own():
    """Baseline. If this ever fails the premise is wrong, not the code."""
    _, cls = _isolated_calibredb()
    seen = {}

    def grab(name):
        seen[name] = id(cls.session_factory())

    for name in ("a", "b"):
        t = threading.Thread(target=grab, args=(name,))
        t.start()
        t.join()

    assert seen["a"] != seen["b"], (
        "scoped_session returned the same Session to two threads — the whole "
        "premise of #1121 depends on it not doing that")


def test_a_thread_keeps_its_own_session_after_another_thread_starts_one():
    """RED until #1121 is fixed.

    The ordering is the point, and it is the real one: a request thread is
    already working when a background task starts. The worker publishes its
    Session over the shared attribute, and the request thread's *next* read of
    `calibre_db.session` silently returns the worker's.
    """
    db, _ = _isolated_calibredb()
    request_ready = threading.Event()
    worker_done = threading.Event()
    seen = {}

    def request_thread():
        db.init_session()
        seen["request_first"] = id(db.session)
        request_ready.set()
        worker_done.wait(5)
        seen["request_second"] = id(db.session)

    def worker_thread():
        request_ready.wait(5)
        db.init_session()
        seen["worker"] = id(db.session)
        worker_done.set()

    threads = [threading.Thread(target=request_thread),
               threading.Thread(target=worker_thread)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert seen["request_first"] != seen["worker"], "the two threads must not share a Session"
    assert seen["request_second"] == seen["request_first"], (
        "the request thread's session changed underneath it: it read %s at the "
        "start and %s after the worker ran, which is the worker's (%s). Every "
        "one of the ~270 `calibre_db.session` call sites is exposed to this "
        "(#1121)" % (seen["request_first"], seen["request_second"], seen["worker"]))

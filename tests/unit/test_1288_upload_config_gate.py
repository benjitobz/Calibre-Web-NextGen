# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fork #1288 — the admin's "Enable Uploads" switch must actually disable uploads.

The classic UI gates its navbar upload button on ``role_upload() and
g.allow_upload`` (cps/templates/layout.html), but the switch was never enforced
past the template: ``upload_required`` and both /api/v1 upload endpoints checked
the per-user role alone. So an admin who turned "Enable Uploads" off got a
hidden classic button and nothing else — the SPA still rendered its own Upload
control and the POST still succeeded.

``_server_features`` promises the other half in its own docstring ("mirrors the
Jinja template gates … authoritative enforcement stays server-side on each
endpoint"), so these pin both sides: the flag reaches the SPA, and the endpoints
refuse when it is off.

The predicate **fails closed** and lives in one place (``config_sql.uploads_enabled``).
``ConfigSQL`` always defines ``config_uploading`` as a mapped Column, so a config
object without it is broken, not an admin decision, and an authorization
boundary must not read a defect as consent. The ``default=1`` on that Column
governs what value a new *row* gets — it says nothing about what an absent
*attribute* means. (Cross-family review on #1295 flagged the original fail-open
reading; this is the corrected one.) Client compatibility is a separate
question and stays permissive on the frontend: an absent ``features.uploading``
JSON key means the peer server predates the flag.
"""
import inspect
import io
import json
import flask
import pytest
from types import SimpleNamespace
from unittest.mock import patch


def _ctx(files=None, path="/api/v1/upload"):
    app = flask.Flask(__name__)
    app.config["WTF_CSRF_ENABLED"] = False
    data = {}
    if files:
        data["file"] = files
    return app.test_request_context(
        path, method="POST", data=data, content_type="multipart/form-data")


def _uploader(role_upload=True, anon=False):
    return SimpleNamespace(is_authenticated=True, is_anonymous=anon,
                           role_upload=lambda: role_upload, id=1, name="alice")


def _cfg(uploading=1):
    return SimpleNamespace(config_uploading=uploading,
                           config_upload_formats="epub,pdf")


# ── /api/v1/upload ───────────────────────────────────────────────────────────

@pytest.mark.unit
def test_upload_refused_when_uploading_disabled():
    """RED before the fix: role alone was enough, so this returned 400/200."""
    from cps.api import upload as mod
    with _ctx(files=[(io.BytesIO(b"x"), "a.epub")]):
        with patch.object(mod, "current_user", _uploader()), \
             patch.object(mod, "config", _cfg(uploading=0)), \
             patch.object(mod, "_ensure_ingest_dir_writable", return_value=None):
            resp = inspect.unwrap(mod.upload_books)()
    assert resp[1] == 403
    assert json.loads(resp[0].get_data())["error"]["code"] == "uploads_disabled"


@pytest.mark.unit
def test_upload_allowed_when_uploading_enabled():
    """The gate must not block the normal path — enabled + role ⇒ past the check."""
    from cps.api import upload as mod
    with _ctx(files=None):
        with patch.object(mod, "current_user", _uploader()), \
             patch.object(mod, "config", _cfg(uploading=1)), \
             patch.object(mod, "_ensure_ingest_dir_writable", return_value=None):
            resp = inspect.unwrap(mod.upload_books)()
    # No files ⇒ 400 invalid_request. Reaching *that* proves the upload gate
    # let us through; a 403 here would mean the new check is too aggressive.
    assert resp[1] == 400
    assert json.loads(resp[0].get_data())["error"]["code"] == "invalid_request"


@pytest.mark.unit
def test_upload_refused_when_setting_absent():
    """Fail closed: a config object missing the attribute is a defect, not consent.

    ConfigSQL declares config_uploading as a Column, so this state cannot arise
    from an admin choice — only from a half-built config, a partial backport, or
    a future refactor. An enforcement boundary should surface that, not grant on
    it.
    """
    from cps.api import upload as mod
    with _ctx(files=[(io.BytesIO(b"x"), "a.epub")]):
        with patch.object(mod, "current_user", _uploader()), \
             patch.object(mod, "config", SimpleNamespace(config_upload_formats="epub")), \
             patch.object(mod, "_ensure_ingest_dir_writable", return_value=None):
            resp = inspect.unwrap(mod.upload_books)()
    assert resp[1] == 403
    assert json.loads(resp[0].get_data())["error"]["code"] == "uploads_disabled"


@pytest.mark.unit
def test_upload_role_check_still_precedes_config_check():
    """A user without the role gets 'forbidden', not 'uploads_disabled' — the
    two refusals stay distinguishable so the SPA can explain the right one."""
    from cps.api import upload as mod
    with _ctx(files=[(io.BytesIO(b"x"), "a.epub")]):
        with patch.object(mod, "current_user", _uploader(role_upload=False)), \
             patch.object(mod, "config", _cfg(uploading=0)):
            resp = inspect.unwrap(mod.upload_books)()
    assert resp[1] == 403
    assert json.loads(resp[0].get_data())["error"]["code"] == "forbidden"


# ── /api/v1/books/<id>/formats (add-format shares the same switch) ───────────

@pytest.mark.unit
def test_add_format_refused_when_uploading_disabled():
    from cps.api import upload as mod
    with _ctx(files=[(io.BytesIO(b"x"), "a.epub")], path="/api/v1/books/1/formats"):
        with patch.object(mod, "current_user", _uploader()), \
             patch.object(mod, "config", _cfg(uploading=0)), \
             patch.object(mod, "calibre_db", SimpleNamespace(get_book=lambda _i: object())):
            resp = inspect.unwrap(mod.add_format)(1)
    assert resp[1] == 403
    assert json.loads(resp[0].get_data())["error"]["code"] == "uploads_disabled"


# ── legacy upload_required (decorates the classic /upload route only) ───────

@pytest.mark.unit
def test_upload_required_aborts_when_uploading_disabled():
    """The classic route hid its button but still served the POST."""
    from cps import editbooks as mod
    from werkzeug.exceptions import Forbidden

    called = []
    guarded = mod.upload_required(lambda: called.append(1))
    with _ctx():
        with patch.object(mod, "current_user", _uploader()), \
             patch.object(mod, "config", _cfg(uploading=0)):
            with pytest.raises(Forbidden):
                guarded()
    assert called == []


@pytest.mark.unit
def test_upload_required_passes_when_enabled():
    from cps import editbooks as mod

    called = []
    guarded = mod.upload_required(lambda: called.append(1) or "ok")
    with _ctx():
        with patch.object(mod, "current_user", _uploader()), \
             patch.object(mod, "config", _cfg(uploading=1)):
            assert guarded() == "ok"
    assert called == [1]


# ── the flag the SPA gates its UI on ─────────────────────────────────────────

@pytest.mark.unit
def test_server_features_exposes_uploading():
    """Without this the SPA cannot hide a control the server will refuse."""
    from cps.api import auth as mod
    cfg = SimpleNamespace(config_user_hide_enabled=False, config_public_reg=False,
                          config_anonbrowse=False, config_kobo_sync=False,
                          config_kobo_sync_magic_shelves=False,
                          config_uploading=1,
                          get_mail_server_configured=lambda: False)
    with patch.object(mod, "config", cfg):
        assert mod._server_features()["uploading"] is True

    cfg.config_uploading = 0
    with patch.object(mod, "config", cfg):
        assert mod._server_features()["uploading"] is False


@pytest.mark.unit
def test_server_features_uploading_matches_the_enforcement_gate():
    """The advertised flag must equal what the endpoints will do, including in
    the fail-closed case — otherwise the SPA offers an upload the server
    refuses, which is the exact split #1288 was about."""
    from cps.api import auth as mod
    from cps.config_sql import uploads_enabled
    broken = SimpleNamespace(get_mail_server_configured=lambda: False)
    with patch.object(mod, "config", broken):
        assert mod._server_features()["uploading"] is False
    assert uploads_enabled(broken) is False

# Calibre-Web Automated – fork of Calibre-Web
# Copyright (C) 2024-2026 Calibre-Web-NextGen contributors
# SPDX-License-Identifier: GPL-3.0-or-later
# See CONTRIBUTORS for full list of authors.

"""#1104 — /robots.txt was a guaranteed 404 in every deployment.

The route is inherited from janeczku/calibre-web and points at a file that has
never existed in either repo, so every install answered 404. A crawler that
gets no answer applies its own default and crawls what it can reach; with
anonymous browsing enabled that is the catalogue, and by extension what the
people using the server read.

The shipped policy disallows everything. Publishing a library is a deliberate
act, so it gets a documented override (a robots.txt in the config directory)
rather than requiring a code change.
"""

from __future__ import annotations

import pathlib
import re

import pytest

pytestmark = pytest.mark.unit

REPO = pathlib.Path(__file__).resolve().parents[2]
SHIPPED = REPO / "cps" / "static" / "robots.txt"


def test_the_file_the_route_serves_actually_exists():
    """The whole bug: the route was real, the file was not."""
    assert SHIPPED.is_file(), (
        "cps/static/robots.txt is missing, so GET /robots.txt 404s again (#1104)")


def test_the_default_policy_disallows_crawling():
    body = SHIPPED.read_text(encoding="utf-8")
    directives = [ln.strip() for ln in body.splitlines()
                  if ln.strip() and not ln.strip().startswith("#")]
    assert any(re.fullmatch(r"User-agent:\s*\*", d, re.I) for d in directives), directives
    assert any(re.fullmatch(r"Disallow:\s*/", d, re.I) for d in directives), (
        "the shipped policy must disallow crawling — an indexed personal "
        "library exposes what its users read (#1104): %r" % directives)


def test_an_admin_supplied_policy_wins_over_the_shipped_one(tmp_path, monkeypatch):
    """Behavioural: the route must choose the config-dir file when present.

    Asserted by driving the route's own directory selection rather than reading
    the source, so a refactor that keeps the comment but drops the check fails.
    """
    from cps import constants, web

    override_dir = tmp_path / "config"
    override_dir.mkdir()
    monkeypatch.setattr(constants, "CONFIG_DIR", str(override_dir))

    chosen = {}

    def fake_send(directory, filename):
        chosen["dir"] = str(directory)
        chosen["file"] = filename
        return "sent"

    monkeypatch.setattr(web, "send_from_directory", fake_send)

    # No override present -> the shipped default.
    web.get_robots()
    assert chosen["dir"] == constants.STATIC_DIR
    assert chosen["file"] == "robots.txt"

    # Override present -> it wins.
    (override_dir / "robots.txt").write_text("User-agent: *\nAllow: /\n", encoding="utf-8")
    web.get_robots()
    assert chosen["dir"] == str(override_dir), (
        "an admin-supplied robots.txt in the config dir must take precedence, "
        "or publishing a library deliberately would need a code change (#1104)")

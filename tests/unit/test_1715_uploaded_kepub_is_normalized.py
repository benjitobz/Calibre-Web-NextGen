# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""An uploaded KEPUB must be normalized on the way in (#1715).

A KEPUB uploaded directly never passes through ``convert.py``, and the repair
task is one-shot per ``REPAIR_VERSION``, so without normalizing here the package
keeps its fragment-anchored TOC targets forever. A Kobo then derives the chapter
id from the TOC entry verbatim -- fragment included -- and files every highlight
made in that chapter under an id no ``ContentType=9`` row carries, so the marks
are stored and drawn nowhere.

These tests drive the real ``upload_book_formats`` with fakes for its module-level
collaborators, so they exercise the actual code path rather than pinning source.
"""
import os
import zipfile

import pytest

pytestmark = pytest.mark.unit

CONTAINER = ('<?xml version="1.0"?><container version="1.0" '
             'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
             '<rootfile full-path="OEBPS/content.opf" '
             'media-type="application/oebps-package+xml"/></rootfiles></container>')
OPF = ('<?xml version="1.0" encoding="UTF-8"?>'
       '<package xmlns="http://www.idpf.org/2007/opf" version="2.0" unique-identifier="i">'
       '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>t</dc:title></metadata>'
       '<manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>'
       '<item id="c1" href="chapter.xhtml" media-type="application/xhtml+xml"/></manifest>'
       '<spine toc="ncx"><itemref idref="c1"/></spine></package>')
NCX = ('<?xml version="1.0" encoding="UTF-8"?>'
       '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><navMap>'
       '<navPoint id="n1" playOrder="1"><navLabel><text>One</text></navLabel>'
       '<content src="chapter.xhtml#top"/></navPoint></navMap></ncx>')
CHAPTER = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>c</title></head>'
           '<body><div id="top">x</div></body></html>')


def _make_kepub(path):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", CONTAINER)
        archive.writestr("OEBPS/content.opf", OPF)
        archive.writestr("OEBPS/toc.ncx", NCX)
        archive.writestr("OEBPS/chapter.xhtml", CHAPTER)
    return path


def _ncx_sources(path):
    from lxml import etree
    with zipfile.ZipFile(path) as archive:
        doc = etree.fromstring(archive.read("OEBPS/toc.ncx"))
    return [e.get("src") for e in doc.iter("{*}content")]


class _Upload:
    """Stands in for a werkzeug FileStorage."""
    def __init__(self, source, filename):
        self._source = source
        self.filename = filename

    def save(self, destination):
        with open(self._source, "rb") as src, open(destination, "wb") as dst:
            dst.write(src.read())


def _drive_upload(monkeypatch, tmp_path, filename):
    """Run the real upload_book_formats with fake collaborators."""
    from cps import editbooks

    library = tmp_path / "library"
    (library / "Author" / "Book (1)").mkdir(parents=True)
    source = _make_kepub(str(tmp_path / "incoming.kepub"))

    recorded = {}

    class _Config:
        config_check_extensions = False
        config_upload_formats = "epub,kepub"
        config_rarfile_location = ""
        def get_book_path(self):
            return str(library)

    class _User:
        name = "tester"
        def role_upload(self):
            return True

    class _Session:
        def add(self, row):
            recorded["row"] = row
        def commit(self):
            pass
        def rollback(self):
            pass

    class _CalibreDb:
        session = _Session()
        def get_book_format(self, book_id, fmt):
            return None

    class _Data:
        def __init__(self, book_id, fmt, size, name):
            self.book_id, self.format, self.uncompressed_size, self.name = book_id, fmt, size, name

    class _Book:
        id = 1
        title = "Book"
        path = "Author/Book (1)"

    monkeypatch.setattr(editbooks, "config", _Config(), raising=False)
    monkeypatch.setattr(editbooks, "current_user", _User(), raising=False)
    monkeypatch.setattr(editbooks, "calibre_db", _CalibreDb(), raising=False)
    monkeypatch.setattr(editbooks.db, "Data", _Data, raising=False)
    monkeypatch.setattr(editbooks, "flash", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(editbooks, "_", lambda text, **k: text, raising=False)
    monkeypatch.setattr(editbooks, "validate_mime_type", lambda *a, **k: True, raising=False)
    # Everything past the write is bookkeeping we are not exercising here.
    monkeypatch.setattr(editbooks, "url_for", lambda *a, **k: "/b/1", raising=False)
    monkeypatch.setattr(editbooks, "escape", lambda v: v, raising=False)
    monkeypatch.setattr(editbooks, "N_", lambda text, **k: text, raising=False)
    monkeypatch.setattr(editbooks.WorkerThread, "add", staticmethod(lambda *a, **k: None), raising=False)
    monkeypatch.setattr(editbooks, "TaskUpload", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(editbooks.uploader, "process", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(editbooks, "merge_metadata", lambda *a, **k: None, raising=False)

    editbooks.upload_book_formats([_Upload(source, filename)], _Book(), 1)

    stored = library / "Author" / "Book (1)" / ("Book (1)." + filename.rsplit(".", 1)[-1])
    return stored, recorded


def test_uploaded_kepub_has_its_redundant_toc_fragment_stripped(monkeypatch, tmp_path):
    stored, _recorded = _drive_upload(monkeypatch, tmp_path, "incoming.kepub")

    assert stored.exists(), "the upload should still be stored"
    assert _ncx_sources(str(stored)) == ["chapter.xhtml"], (
        "the redundant #top fragment must be gone, or a Kobo files highlights in "
        "this chapter under an id no spine row carries")


def test_recorded_size_matches_the_normalized_file(monkeypatch, tmp_path):
    """Size is measured AFTER normalization -- it rewrites the archive."""
    stored, recorded = _drive_upload(monkeypatch, tmp_path, "incoming.kepub")

    assert "row" in recorded, "a Calibre data row should have been added"
    assert recorded["row"].uncompressed_size == os.path.getsize(str(stored)), (
        "uncompressed_size was measured before normalization rewrote the file")


def test_non_kepub_upload_is_untouched(monkeypatch, tmp_path):
    """An .epub must not be run through the KEPUB normalizer."""
    stored, _recorded = _drive_upload(monkeypatch, tmp_path, "incoming.epub")

    assert stored.exists()
    assert _ncx_sources(str(stored)) == ["chapter.xhtml#top"], (
        "a non-KEPUB upload must be stored byte-for-byte as supplied")

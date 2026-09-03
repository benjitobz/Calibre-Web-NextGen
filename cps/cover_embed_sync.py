# CWA-NG service: embed the calibre record's cover into every book-file format.
#
# calibre's embed_metadata (used at ingest and via the content server) does NOT
# rewrite covers already present in Kindle formats (azw3/mobi), and it can leave
# an epub carrying a stale cover. When an external manager (e.g. Chaptarr) writes
# a canonical cover to a book's cover.jpg, the format files keep whatever cover
# they were converted with. This periodic sweep uses `ebook-polish --cover`,
# which replaces the cover in place across epub/azw3/mobi/kepub, to make every
# format match the calibre cover.jpg. It only acts when cover.jpg has changed
# since the last pass (mtime state), so steady state is a cheap no-op.
import glob
import json
import os
import subprocess

from cps import config, logger
from cps.cover_dedup import dedupe_cover_pages
from cps.cover_normalize import normalize_cover

log = logger.create()

COVER_SYNC_BATCH_LIMIT = 100
POLISH_TIMEOUT_SECONDS = 120
_FORMATS = ("epub", "azw3", "mobi", "kepub")


def _reconvert_cover(target_path, cover_path):
    """Regenerate a non-editable format (old MOBI) from a sibling so it carries
    the canonical cover. Converts the book's epub/azw3 to the target format with
    --cover, replacing the file in place. Returns True on success."""
    base, ext = os.path.splitext(target_path)
    book_dir = os.path.dirname(target_path)

    source = None
    for cand_ext in (".epub", ".azw3"):
        matches = glob.glob(os.path.join(book_dir, "*" + cand_ext))
        if matches:
            source = matches[0]
            break

    if not source:
        return False

    tmp_out = base + ".coversync" + ext
    try:
        result = subprocess.run(
            ["ebook-convert", source, tmp_out, "--cover", cover_path],
            capture_output=True,
            timeout=POLISH_TIMEOUT_SECONDS * 3,
        )
        if result.returncode == 0 and os.path.exists(tmp_out):
            os.replace(tmp_out, target_path)
            return True
        log.warning("Cover re-convert failed for %s: %s", target_path, result.stderr.decode("utf-8", "ignore")[:200])
    except Exception as exc:
        log.warning("Cover re-convert error for %s: %s", target_path, exc)
    finally:
        if os.path.exists(tmp_out):
            try:
                os.remove(tmp_out)
            except Exception:
                pass
    return False


def _rebuild_siblings_from_epub(epub_path, sibling_paths, cover_path):
    """Regenerate Kindle formats from a cleaned epub. The duplicate cover page
    lives in every format, and only the epub can be edited in place, so the
    siblings are re-derived from it."""
    rebuilt = 0

    for target in sibling_paths:
        ext = os.path.splitext(target)[1]
        tmp_out = os.path.splitext(target)[0] + ".coverdedup" + ext

        try:
            result = subprocess.run(
                ["ebook-convert", epub_path, tmp_out, "--cover", cover_path],
                capture_output=True,
                timeout=POLISH_TIMEOUT_SECONDS * 3,
            )
            if result.returncode == 0 and os.path.exists(tmp_out) and os.path.getsize(tmp_out) > 1024:
                os.replace(tmp_out, target)
                rebuilt += 1
            else:
                log.warning("Cover-page rebuild failed for %s: %s", target, result.stderr.decode("utf-8", "ignore")[:200])
        except Exception as exc:
            log.warning("Cover-page rebuild error for %s: %s", target, exc)
        finally:
            if os.path.exists(tmp_out):
                try:
                    os.remove(tmp_out)
                except Exception:
                    pass

    return rebuilt


def sync_embedded_covers():
    import sqlite3

    try:
        library_dir = config.config_calibre_dir

        if not library_dir:
            return 0

        metadata_db = os.path.join(library_dir, "metadata.db")

        if not os.path.exists(metadata_db):
            return 0

        with sqlite3.connect("file:%s?mode=ro" % metadata_db, uri=True, timeout=10) as con:
            rows = con.execute("SELECT id, path FROM books").fetchall()

        state_file = os.path.join("/config", ".cwa_cover_embed.json")

        try:
            with open(state_file, "r", encoding="utf-8") as handle:
                state = json.load(handle)
        except Exception:
            state = {}

        polished_books = 0
        processed = 0

        for book_id, rel_path in rows:
            if processed >= COVER_SYNC_BATCH_LIMIT:
                break

            book_dir = os.path.join(library_dir, rel_path)
            cover = os.path.join(book_dir, "cover.jpg")

            if not os.path.exists(cover):
                continue

            # Uniform canvas first: the embedded copies below should carry the
            # same normalized cover the library grid shows.
            normalize_cover(cover)

            cover_mtime = os.path.getmtime(cover)
            key = str(book_id)

            # Already embedded this exact cover into this book's formats.
            if state.get(key) == cover_mtime:
                continue

            processed += 1

            files = []
            for ext in _FORMATS:
                files.extend(glob.glob(os.path.join(book_dir, "*." + ext)))

            if not files:
                state[key] = cover_mtime
                continue

            # Some source books ship a second full-page cover image as ordinary
            # front matter. Swapping the designated cover leaves that page
            # showing the old art, so the reader displays two different covers.
            epubs = [f for f in files if f.lower().endswith(".epub")]
            siblings = [f for f in files if not f.lower().endswith(".epub")]

            for epub in epubs:
                try:
                    if dedupe_cover_pages(epub):
                        log.info("Removed a duplicate cover page from %s", epub)
                        if siblings:
                            _rebuild_siblings_from_epub(epub, siblings, cover)
                except Exception as exc:
                    log.warning("Cover-page dedupe failed for %s: %s", epub, exc)

            editable_ok = True
            for path in files:
                # ebook-polish cannot edit old (non-KF8) MOBI files; regenerate the
                # cover for those by re-converting from a canonical sibling instead.
                is_legacy_mobi = path.lower().endswith(".mobi")

                try:
                    result = subprocess.run(
                        ["ebook-polish", "--cover", cover, path, path],
                        capture_output=True,
                        timeout=POLISH_TIMEOUT_SECONDS,
                    )
                    if result.returncode == 0:
                        continue

                    err = result.stderr.decode("utf-8", "ignore")

                    if is_legacy_mobi or "KF8" in err or "InvalidMobi" in err:
                        if not _reconvert_cover(path, cover):
                            log.info("Cover for non-editable format left as-is: %s", path)
                        continue

                    editable_ok = False
                    log.warning("Cover embed failed for %s: %s", path, err[:200])
                except Exception as exc:
                    if is_legacy_mobi:
                        continue
                    editable_ok = False
                    log.warning("Cover embed error for %s: %s", path, exc)

            # Record when the editable formats succeeded so we do not re-polish a
            # book every pass; a genuine failure on an editable format retries.
            if editable_ok:
                state[key] = cover_mtime
                polished_books += 1

        try:
            with open(state_file, "w", encoding="utf-8") as handle:
                json.dump(state, handle)
        except Exception as exc:
            log.error("Unable to persist cover-embed state: %s", exc)

        if polished_books:
            log.info("Embedded canonical covers into the formats of %d book(s)", polished_books)

        return polished_books
    except Exception as exc:
        log.error("Cover embed sweep failed: %s", exc)
        return 0

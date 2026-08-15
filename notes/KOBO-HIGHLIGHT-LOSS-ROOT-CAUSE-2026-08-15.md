# Kobo highlight loss — two independent root causes, both measured on hardware

**Date:** 2026-08-15 · **Reporter:** household instance · **Symptom as reported:**
*"On a Kobo reading a KEPUB, I highlight something, leave the book, come back, and the highlight is gone."*

Everything below was measured, not inferred. The device DB was read over USB from
`/Volumes/KOBOeReader/.kobo/KoboReader.sqlite` (copy it off first — `sqlite3` `mode=ro`
against the mounted volume fails with *unable to open database file*).

---

## Cause 1 — highlights never render: a `../` OPF href splits the chapter identity

Kobo identifies a chapter as `<book-uuid>!<opf-dir>!<href>`. A `Bookmark` renders only when
`Bookmark.ContentID` **exactly equals** a `content` row with `ContentType=9`. No fuzzy matching.

The affected book's OPF (`OPS/epb.opf`) declared the EPUB3 nav at the ZIP ROOT:

```xml
<item id="nav" href="../nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
```

Nickel joins `opf_dir + href` **without normalizing `..`**, so:

```
nav path        = "OPS/../nav.xhtml"
nav's directory = "OPS/.."
nav link        = "OPS/chapter-017.xml"
joined          = "OPS/../OPS/chapter-017.xml"   <- what got written to Bookmark.ContentID
```

Both identities are visible side by side in `content`:

| ContentType | ContentID |
|---|---|
| 9 (chapter, used for rendering) | `<uuid>!OPS!chapter-017.xml` |
| 899 (TOC entry) | `<uuid>!OPS!../OPS/chapter-017.xml-2` |

All 88 of the book's `Bookmark` rows carried the TOC-derived form → **0 matches** → nothing drawn.
A control book returned `matching content row exists: 1`; this book returned `0`.

**Census query — orphaned highlights, device-wide:**

```sql
select b.VolumeID, count(*) n,
  sum(case when exists(select 1 from content c where c.ContentID=b.ContentID) then 1 else 0 end) matched
from Bookmark b group by b.VolumeID;
```

907 of 3016 were orphaned. Two distinct reasons: this bug (Flatland 88, All Quiet 9), and
*book no longer on the device* (Iliad 584, King in Yellow 220, Republic 6 — those have **no**
`ContentType=9` rows at all and are not repairable by rewriting; they'd render again if the book
returned to the same path).

**Library scan:** 5 of 216 books (2.3%) carry an escaping OPF href, every one `../nav.xhtml`.
Perfect correlation on the instance: every book with one has **zero** stored annotations ever;
all 604 stored annotations come from books without one.

**Repair** (device in USB mass-storage mode, Nickel not running): `UPDATE Bookmark SET ContentID`
to `<uuid>!<opf-dir>!<normpath(href)>`, scoped by `VolumeID`. Verify each target has a
`ContentType='9'` row first; hash every other volume's rows before/after to prove isolation. PK is
`BookmarkID`, so rewriting `ContentID` cannot collide. 88/88 restored this way.

**Durable fix:** normalize escaping manifest hrefs during EPUB→KEPUB conversion (PR #1637).

---

## Cause 2 — highlights are then DELETED: the annotation download returns an authoritative empty set

This is upstream [calibre-web #2610](https://github.com/janeczku/calibre-web/issues/2610), open
since 2022 and never root-caused because nobody had read the device DB during the event.

CWNG advertises itself as `reading_services_host` whenever Kobo sync is on, then forwards
`GET /api/v3/content/<uuid>/annotations` to `readingservices.kobo.com`. **Kobo's cloud has never
heard of a sideloaded book**, so it answers with a success-shaped empty set — and Nickel acts on
that by deleting every local `Bookmark` row for the book.

```
11:58   88 highlights present and correctly anchored (after the Cause-1 repair)
12:07   one sync:  1 annotation PATCHed up
                   GET .../annotations?limit=100  -> proxied to Kobo
after   0 Bookmark rows for that book
```

**87 deleted, never uploaded.** The server held exactly 1 of 88. For a sideloaded book the device is
frequently the only copy, so a 200-empty here is a **destructive operation even though the verb is
GET**.

⚠️ **Repairing Cause 1 EXPOSES annotations to Cause 2.** While the ContentIDs were malformed the
rows were invisible to the sync reconciliation and survived; making them well-formed made them
eligible for deletion. **Fix the server before repairing a device.**

**Fix (PR #1636):** when the entitlement resolves to a book we own, do not proxy the download
direction — return **503 + `Retry-After`**, the one response that cannot be read as "you have none".
Content we do not own still proxies (there Kobo's cloud genuinely is authoritative); PATCH upload is
untouched.

Deliberately **not** answering with our own snapshot: an internally-complete server view would tell
a second device to drop annotations it has not uploaded yet, and a regenerated KEPUB shifts KoboSpan
ids so a correct-looking snapshot installs misplaced anchors.

---

## Cause 3 (ours) — the server was discarding every Kobo highlight

Independent of the device, #1531 added content-id validation to `_upsert_annotation` whose failure
path was `return None`, discarding the whole annotation over a derived, nullable, recomputable
field. Deployed 2026-08-13 18:38:11; first discard 18:40:37; **95 highlights across 95 distinct ids
destroyed in under 48 hours**, zero annotations stored for any book in that window.

It shipped because the gate was dead code in tests: the dispatcher fixture's `_book()` is a
`SimpleNamespace` with no `uuid`, so the branch was never entered.

Fixed in #1635 (never discard) and #1638 (normalize contained traversals so `content_id` is correct
rather than NULL). The validator was *right* that the value was malformed — it was the canary for
Cause 1 — but it was wrong to charge the user for the discovery.

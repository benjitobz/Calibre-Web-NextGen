# CWA-NG helper: strip a redundant image-only cover page from an epub.
#
# Some source books (notably Anna's Archive rips) ship two front-matter pages
# that each render a full-page cover image: the calibre-designated cover page
# plus a separate "title" page carrying the ORIGINAL cover art. ebook-polish and
# calibre's embed_metadata only swap the designated cover, so the leftover page
# keeps showing the old cover -- the reader then displays two different covers.
# This removes that leftover page (and its orphaned image) so every format has a
# single canonical cover once the sibling formats are rebuilt from the epub.
import io
import posixpath
import re
import shutil
import zipfile

try:
    from PIL import Image
except Exception:  # pragma: no cover - Pillow ships with the image, but stay safe
    Image = None

_COVERLIKE = re.compile(r'(cover|title)', re.I)


def _phash(data):
    if Image is None:
        return None
    try:
        im = Image.open(io.BytesIO(data)).convert('L').resize((12, 12))
        px = list(im.get_flattened_data())
        avg = sum(px) / len(px)
        return ''.join('1' if p > avg else '0' for p in px)
    except Exception:
        return None


def _hamming(a, b):
    if not a or not b:
        return 99
    return sum(x != y for x, y in zip(a, b))


def dedupe_cover_pages(path):
    """Remove redundant image-only cover/title pages from an epub, keeping the
    designated calibre cover page. Returns True if the epub was modified."""
    z = zipfile.ZipFile(path)
    names = z.namelist()
    opfs = [n for n in names if n.lower().endswith('.opf')]
    if not opfs:
        z.close()
        return False
    opf_name = opfs[0]
    opf_dir = posixpath.dirname(opf_name)
    opf = z.read(opf_name).decode('utf-8', 'ignore')

    manifest = {}
    for m in re.finditer(r'<item\b([^>]*?)/?>', opf):
        attrs = m.group(1)
        iid = re.search(r'\bid=["\']([^"\']+)', attrs)
        href = re.search(r'\bhref=["\']([^"\']+)', attrs)
        mt = re.search(r'\bmedia-type=["\']([^"\']+)', attrs)
        if iid and href:
            manifest[iid.group(1)] = (href.group(1), mt.group(1) if mt else '')
    spine = re.findall(r'<itemref[^>]*idref=["\']([^"\']+)', opf)

    def full(href):
        return posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else posixpath.normpath(href)

    def resolve(base_href, rel):
        return posixpath.normpath(posixpath.join(posixpath.dirname(full(base_href)), rel))

    guide = re.search(r'<reference[^>]*type=["\']cover["\'][^>]*href=["\']([^"\']+)', opf)
    cover_page = posixpath.normpath(guide.group(1)) if guide else None
    meta_cover = (re.search(r'<meta[^>]*name=["\']cover["\'][^>]*content=["\']([^"\']+)', opf)
                  or re.search(r'<meta[^>]*content=["\']([^"\']+)["\'][^>]*name=["\']cover["\']', opf))
    canon_img = full(manifest[meta_cover.group(1)][0]) if (meta_cover and meta_cover.group(1) in manifest) else None
    canon_hash = _phash(z.read(canon_img)) if (canon_img and canon_img in names) else None

    remove_ids = []
    orphan = set()
    for sid in spine[:4]:
        if sid not in manifest:
            continue
        href, mt = manifest[sid]
        if 'html' not in mt.lower() and not href.lower().endswith(('xhtml', 'html', 'htm')):
            continue
        f = full(href)
        if cover_page and f == cover_page:
            continue
        try:
            page = z.read(f).decode('utf-8', 'ignore')
        except Exception:
            continue
        # never touch the page calibre marks as its own cover
        if re.search(r'<meta[^>]*calibre:cover[^>]*content=["\']true', page, re.I):
            continue
        body = re.search(r'<body[^>]*>(.*?)</body>', page, re.S | re.I)
        body_html = body.group(1) if body else page
        imgs = re.findall(r'(?:src|xlink:href)=["\']([^"\']+\.(?:jpg|jpeg|png|gif))["\']', body_html, re.I)
        text = re.sub(r'\s+', '', re.sub(r'<[^>]+>', '', body_html))
        if not imgs or len(text) >= 40:
            continue
        resolved = [resolve(href, im) for im in imgs]
        cover_like = bool(_COVERLIKE.search(sid) or _COVERLIKE.search(href)
                          or any(_COVERLIKE.search(posixpath.basename(r)) for r in resolved))
        if not cover_like:
            continue
        differs = True
        if canon_hash:
            for r in resolved:
                if r in names and _hamming(canon_hash, _phash(z.read(r))) <= 3:
                    differs = False
        if differs:
            remove_ids.append(sid)
            orphan.update(resolved)

    if not remove_ids:
        z.close()
        return False

    remove_hrefs = {full(manifest[i][0]) for i in remove_ids}
    still_ref = set()
    for n in names:
        if n.lower().endswith(('xhtml', 'html', 'htm')) and posixpath.normpath(n) not in remove_hrefs:
            try:
                c = z.read(n).decode('utf-8', 'ignore')
            except Exception:
                continue
            for im in re.findall(r'(?:src|xlink:href)=["\']([^"\']+\.(?:jpg|jpeg|png|gif))["\']', c, re.I):
                still_ref.add(posixpath.normpath(posixpath.join(posixpath.dirname(n), im)))
    drop_imgs = {o for o in orphan if o not in still_ref and o != canon_img}

    new_opf = opf
    for i in remove_ids:
        new_opf = re.sub(r'<itemref[^>]*idref=["\']%s["\'][^>]*/?>\s*' % re.escape(i), '', new_opf)
        new_opf = re.sub(r'<item\b[^>]*\bid=["\']%s["\'][^>]*/?>\s*' % re.escape(i), '', new_opf)
    for img in drop_imgs:
        rel = posixpath.relpath(img, opf_dir) if opf_dir else img
        new_opf = re.sub(r'<item\b[^>]*\bhref=["\']%s["\'][^>]*/?>\s*' % re.escape(rel), '', new_opf)

    remove_files = set(remove_hrefs) | drop_imgs
    tmp = path + '.dedup'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as out:
        if 'mimetype' in names:
            out.writestr('mimetype', z.read('mimetype'), zipfile.ZIP_STORED)
        for n in names:
            if n == 'mimetype' or posixpath.normpath(n) in remove_files:
                continue
            data = new_opf.encode('utf-8') if n == opf_name else z.read(n)
            out.writestr(n, data)
    z.close()
    shutil.move(tmp, path)
    return True

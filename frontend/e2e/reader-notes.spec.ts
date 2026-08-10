import { expect, test, type Page } from '@playwright/test';

/*
 * #325 — notes attached to web-reader highlights, and the in-reader
 * "Highlights and notes" drawer that lists them.
 *
 * Both live in one file on purpose. Each test drives a whole epub.js reader,
 * and this container starves when several do so at once — the same reason
 * reader-phase1 and reader-rtl each declare serial mode. Two spec files meant
 * four concurrent reader sessions and intermittent render timeouts that looked
 * like product failures; one serial file halves that.
 *
 * The backend has accepted `note_text` on create/edit since the annotation
 * subsystem landed; until this feature the SPA reader simply never sent or read
 * it. These tests drive the real reader against the real endpoints and assert on
 * server state, not on component internals.
 *
 * Deliberately content-agnostic: it annotates whatever text the reader happens
 * to render, so it cannot silently skip when the library's newest EPUB changes.
 *
 * Three things about this surface will waste your afternoon if you don't know
 * them (all measured, 2026-08-09):
 *
 *  1. Playwright's synthetic mouse DRAG hangs over the epub.js iframe —
 *     `mouse.down()` succeeds and the following `mouse.move()` never returns.
 *     Reproduced headless and headed. So selection is driven by building a
 *     Range inside the frame and dispatching the events epub.js listens for.
 *     epub.js binds those listeners from the PARENT frame, so this exercises
 *     the real `rendition.on('selected')` path rather than faking the outcome.
 *  2. epub.js paints highlights into a marks-pane SVG overlay that lives in the
 *     PARENT document, positioned over the iframe — NOT inside the book frame.
 *     Querying `.cwng-hl-noted` inside the frame always returns 0 and reads as
 *     "highlights are broken". Query the page.
 *  3. The first page of an EPUB is usually a text-free cover, and the reader
 *     restores a saved position. Page forward until there is real text before
 *     trying to select anything.
 *
 * LOAD SENSITIVITY, stated plainly. Each test renders a whole epub.js reader,
 * and cwn-local is shared. Measured on 2026-08-09: green at --workers=1 and
 * green under the CI retry policy, but under two workers on a busy container a
 * cold first render can miss its timeout and one test fails — a different one
 * each time. That is the same condition reader-rtl documents for itself, and it
 * is why the config sets retries in CI. If you see a single varying failure
 * here, re-run serially before suspecting the reader: a real regression fails
 * the same test every time.
 */

/*
 * Open the LOWEST-id book that has an EPUB and renders.
 *
 * Deliberately not "newest": sibling specs (reader-rtl, discover-upload) upload
 * fixtures that become the newest book, so targeting that made this spec both
 * annotate their fixtures and depend on their ordering. Low ids are the stable
 * seeded library.
 */
async function openReaderOnEpub(page: Page, offset: number): Promise<number | null> {
  /*
   * Choose a small-but-real EPUB, deterministically.
   *
   * Three constraints learned the hard way. Picking "the Nth book that happened
   * to render" slides the index whenever a render is slow, so two tests land on
   * the same book and each clears the other's fixtures. Picking purely by id
   * lands on Don Quixote, which does not render inside the timeout at all.
   * And resolving sizes for every book meant ~100 sequential requests per test,
   * a storm that itself caused the render timeouts and "socket hang up" it was
   * meant to avoid.
   *
   * So: take a bounded head of the id-sorted EPUB list, resolve just those
   * sizes, and order by size. The floor drops the synthetic single-page
   * fixtures that have no prose to select.
   */
  const MIN_BYTES = 60_000;
  // Wide enough that each of the SPEC_SLOTS lanes below still has more than one
  // candidate to fall back to, but still a bounded number of detail requests
  // (~16, against the ~100 that used to cause the very timeouts this avoids).
  const POOL = 16;
  const res = await page.request.get('/api/v1/books?page=1&per_page=100&sort=new');
  const books = await res.json();
  const epubIds = [...(books.items || books.books || [])]
    .filter((bk: { formats?: string[] }) =>
      (bk.formats || []).some((f) => String(f).toLowerCase() === 'epub'))
    .sort((a: { id: number }, b: { id: number }) => a.id - b.id)
    .slice(0, POOL)
    .map((bk: { id: number }) => bk.id);

  const sized: { id: number; size: number }[] = [];
  for (const id of epubIds) {
    const detail = await (await page.request.get(`/api/v1/books/${id}`)).json();
    const epub = (detail.formats || []).find(
      (f: { format: string }) => f.format.toLowerCase() === 'epub');
    if (epub && (epub.size_bytes ?? 0) >= MIN_BYTES) sized.push({ id, size: epub.size_bytes });
  }
  sized.sort((a, b) => a.size - b.size || a.id - b.id);

  /*
   * Give each spec a DIFFERENT first choice, and every book as fallback.
   *
   * Two properties have to hold at once, and a stride filter (i % SLOTS ===
   * offset) satisfied only the first:
   *
   *   - two specs must not START on the same book, because each clears the
   *     other's annotations and then asserts on fixtures that just vanished;
   *   - a spec must always have somewhere to fall back to, because one cold
   *     render can miss even a generous timeout when workers share a container.
   *
   * The stride version selected NOTHING for the higher offsets whenever the
   * seeded library was small — `i % 4 === 3` over three eligible books is empty
   * — so mobile returned null before opening anything, deterministically, and
   * all three CI retries failed identically. That reads exactly like "the reader
   * cannot render an EPUB" while being purely an arithmetic bug here.
   *
   * Rotating the list instead is total: distinct starting points while there are
   * at least as many books as offsets, and the full list available after that.
   */
  if (!sized.length) {
    // Nothing cleared the size floor. Better to try the small fixtures than to
    // report "no EPUB renders", which sends the reader on a hunt for a bug that
    // is really an empty candidate list.
    console.warn(`[reader-notes] no EPUB >= ${MIN_BYTES}B; falling back to all EPUBs`);
    for (const id of epubIds) sized.push({ id, size: 0 });
  }
  const start = offset % sized.length;
  const candidates = [...sized.slice(start), ...sized.slice(0, start)];

  for (const candidate of candidates) {
    // Arrange the known state before the first render, not after it. A previous
    // run that died before cleanup leaves highlights behind, and then "the first
    // noted highlight on the page" is someone else's — which is exactly how this
    // spec once tapped a stale highlight and read its older note.
    await clearAnnotationsViaApi(page, candidate.id);
    // Retry the same book once before moving on: the first reader render in a
    // fresh context pays for the epub.js chunk and the book download at once.
    for (let attempt = 0; attempt < 2; attempt++) {
      await page.goto(`/app/read/${candidate.id}`);
      const rendered = await page.locator('iframe')
        .waitFor({ state: 'visible', timeout: 40_000 })
        .then(() => true).catch(() => false);
      if (rendered) return candidate.id;
    }
  }
  console.warn(`[reader-notes] none of ${candidates.length} candidate EPUB(s) rendered`);
  return null;
}

/** Page forward until the rendered section actually carries selectable text. */
/*
 * Wait for the reader to actually be ready, then find a page with prose.
 *
 * Deliberately condition-based rather than a fixed sleep. Under two concurrent
 * workers this container can take well over the few seconds a sleep assumes,
 * and starting to press ArrowRight against a half-rendered book was the cause
 * of every intermittent failure this spec had: it read an empty frame, paged
 * past the text, and reported "no page with selectable text" as though the
 * product were broken.
 */
async function pageUntilText(page: Page): Promise<boolean> {
  const frameText = async () => {
    const frame = page.frames().find((f) => f !== page.mainFrame());
    if (!frame) return '';
    return await frame.evaluate(() => document.body?.innerText || '').catch(() => '');
  };
  // First: the book has rendered something at all (cover counts).
  const readyBy = Date.now() + 15_000;
  while (Date.now() < readyBy) {
    const frame = page.frames().find((f) => f !== page.mainFrame());
    if (frame) {
      const painted = await frame.evaluate(
        () => (document.body?.innerText || '').length + document.querySelectorAll('img,svg,p,div').length,
      ).catch(() => 0);
      if (painted > 0) break;
    }
    await page.waitForTimeout(500);
  }
  // Then: page forward to prose, allowing each turn time to settle.
  for (let i = 0; i < 10; i++) {
    if ((await frameText()).trim().length > 300) return true;
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(900);
  }
  return false;
}

/** Select a run of text the way a reader would — through epub.js's own handler. */
async function selectSomeText(page: Page): Promise<string> {
  const frame = page.frames().find((f) => f !== page.mainFrame())!;
  return await frame.evaluate(() => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    let node: Node | null = null;
    while ((node = walker.nextNode())) if ((node.textContent || '').trim().length > 60) break;
    if (!node) return '';
    const range = document.createRange();
    range.setStart(node, 0);
    range.setEnd(node, Math.min(70, (node.textContent || '').length));
    const sel = window.getSelection()!;
    sel.removeAllRanges();
    sel.addRange(range);
    document.dispatchEvent(new Event('selectionchange', { bubbles: true }));
    const box = range.getBoundingClientRect();
    for (const type of ['mousedown', 'mouseup'])
      document.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: box.x + 5, clientY: box.y + 5 }));
    return String(sel);
  });
}

const setNote = (page: Page, text: string) => page.evaluate((v) => {
  const ta = document.querySelector('textarea')!;
  Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value')!
    .set!.call(ta, v);
  ta.dispatchEvent(new Event('input', { bubbles: true }));
}, text);

const notesOnServer = (page: Page, bookId: number) => page.evaluate(async (id) => {
  const r = await fetch(`/annotations/${id}/data.json`, { credentials: 'include' });
  return ((await r.json()).annotations || []).map((a: { note_text: string | null }) => a.note_text);
}, bookId);

// Highlights live in the parent document's marks-pane overlay (see header note).
const paintCounts = (page: Page) => page.evaluate(() => ({
  noted: document.querySelectorAll('.cwng-hl-noted').length,
  plain: document.querySelectorAll('.cwng-hl').length,
}));

const annotationIds = (page: Page, bookId: number) => page.evaluate(async (id) => {
  const r = await fetch(`/annotations/${id}/data.json`, { credentials: 'include' });
  return ((await r.json()).annotations || []).map((a: { annotation_id: string }) => a.annotation_id);
}, bookId);

/*
 * Delete every annotation this spec added, leaving the book as it was found.
 * Not optional hygiene: these tests annotate whichever EPUB is newest, which is
 * routinely a fixture another spec owns (reader-rtl uploads one). Without this,
 * a sibling spec inherits our highlights and a moved reading position and fails
 * for reasons that have nothing to do with it.
 */
/* Clear a book's annotations over HTTP, with no page rendered. Used to arrange
 * state BEFORE opening the reader, which saves a whole render per test. */
async function clearAnnotationsViaApi(page: Page, bookId: number) {
  const csrf = (await (await page.request.get('/api/v1/auth/csrf')).json()).csrf_token;
  const rows = (await (await page.request.get(`/annotations/${bookId}/data.json`)).json()).annotations || [];
  for (const a of rows) {
    await page.request.delete(`/annotations/${bookId}/${a.annotation_id}`, {
      headers: { 'X-CSRFToken': csrf },
    });
  }
}

async function restoreAnnotations(page: Page, bookId: number, keep: string[]) {
  await page.evaluate(async ([id, known]) => {
    const csrf = (await (await fetch('/api/v1/auth/csrf', { credentials: 'include' })).json()).csrf_token;
    const r = await fetch(`/annotations/${id}/data.json`, { credentials: 'include' });
    const rows = (await r.json()).annotations || [];
    for (const a of rows) {
      if ((known as string[]).includes(a.annotation_id)) continue;
      await fetch(`/annotations/${id}/${a.annotation_id}`, {
        method: 'DELETE', credentials: 'include', headers: { 'X-CSRFToken': csrf },
      });
    }
  }, [bookId, keep] as [number, string[]]);
}

/** Tap a painted highlight, opening the edit popover. */
const tapHighlight = (page: Page, selector: string) => page.evaluate((sel) => {
  const g = document.querySelector(sel);
  if (!g) throw new Error('no painted highlight matching ' + sel);
  const box = g.getBoundingClientRect();
  for (const type of ['mousedown', 'mouseup', 'click'])
    g.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: box.x + box.width / 2, clientY: box.y + box.height / 2 }));
}, selector);

/*
 * Page forward until the noted highlight is actually on screen.
 *
 * epub.js only paints an annotation into a view it has rendered, and the reader
 * restores a saved position — so "is it repainted after a reload" cannot be
 * asked of whatever page happens to be showing. At 375px the book repaginates
 * and the highlight lands on a different page than it does on desktop, so this
 * failed on mobile ONLY, while the feature worked correctly. The claim under
 * test is that the highlight comes back, not that it comes back on the page the
 * reader happened to open.
 */
async function pageUntilNotedPainted(page: Page, expected: number): Promise<number> {
  for (let i = 0; i < 12; i++) {
    const { noted } = await paintCounts(page);
    if (noted >= expected) return noted;
    await page.keyboard.press('ArrowRight');
    await page.waitForTimeout(900);
  }
  return (await paintCounts(page)).noted;
}

/*
 * Wait for the reader to render after a reload.
 *
 * Same budget as the first open in openReaderOnEpub, deliberately: a reload may
 * reuse the cached epub.js chunk but still re-downloads and re-parses the book,
 * so it is not the cheap operation a shorter timeout assumes. A 25s budget here
 * was the real cause of a "the noted highlight is repainted after a reload"
 * failure on mobile — the iframe never appeared, so the paint assertion below it
 * never ran, and the report named the repaint rather than the render.
 */
async function waitForReaderRender(page: Page): Promise<void> {
  await page.locator('iframe').waitFor({ state: 'visible', timeout: 40_000 });
}

test.describe('reader notes (#325)', () => {
  test.describe.configure({ mode: 'serial' });

  test('a note can be written, survives a reload, is editable and removable', async ({ page }, testInfo) => {
    // Renders the book three times (open, clear+reload, reload); the 45s default
    // is not enough for that when two workers share this container.
    test.setTimeout(120_000);
    const bookId = await openReaderOnEpub(page, testInfo.project.name === 'mobile' ? 1 : 0);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();
    expect(await pageUntilText(page), 'a page with selectable text').toBe(true);

    const preExisting = await annotationIds(page, bookId!);
    expect(preExisting, 'the test book starts with no annotations').toHaveLength(0);
    const before = await paintCounts(page);
    expect(await selectSomeText(page), 'text selected in the book frame').not.toBe('');

    // --- create: highlight + note in a single write ---
    await expect(page.getByRole('button', { name: 'Add note' })).toBeVisible();
    await page.getByRole('button', { name: 'Add note' }).click();
    await expect(page.locator('textarea')).toBeFocused();

    const NOTE = `Frame narrative established here. ${Date.now()}`;
    await setNote(page, NOTE);
    await page.getByRole('button', { name: 'Save note' }).click();

    await expect.poll(() => notesOnServer(page, bookId!)).toContain(NOTE);
    // Painted straight away, and marked as carrying a note.
    await expect.poll(async () => (await paintCounts(page)).noted).toBe(before.noted + 1);

    // --- survives a reload ---
    await page.reload();
    await waitForReaderRender(page);
    await expect(page.getByRole('button', { name: 'Highlights and notes' })).toBeVisible();
    expect(
      await pageUntilNotedPainted(page, before.noted + 1),
      'the noted highlight is repainted after a reload',
    ).toBe(before.noted + 1);
    expect(await notesOnServer(page, bookId!)).toContain(NOTE);

    // Tapping the highlight reveals the note without opening the composer.
    await tapHighlight(page, '.cwng-hl-noted');
    await expect(page.getByRole('dialog', { name: 'Highlight color' })).toContainText(NOTE);

    // --- edit ---
    await page.getByRole('button', { name: 'Edit note' }).click();
    const EDITED = `Edited: the narrator is introduced. ${Date.now()}`;
    await setNote(page, EDITED);
    await page.getByRole('button', { name: 'Save note' }).click();
    await expect.poll(() => notesOnServer(page, bookId!)).toContain(EDITED);

    // --- remove the note; the highlight itself must survive ---
    await tapHighlight(page, '.cwng-hl-noted');
    await page.getByRole('button', { name: 'Edit note' }).click();
    await page.getByRole('button', { name: 'Remove note' }).click();

    await expect.poll(() => notesOnServer(page, bookId!)).not.toContain(EDITED);
    // The note marker is gone but the highlight is still painted.
    await expect.poll(async () => (await paintCounts(page)).noted).toBe(before.noted);
    expect((await paintCounts(page)).plain).toBe(before.plain + 1);

    await restoreAnnotations(page, bookId!, preExisting);
  });

  test('the one-tap colour highlight still creates without a note', async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const bookId = await openReaderOnEpub(page, testInfo.project.name === 'mobile' ? 1 : 0);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();
    expect(await pageUntilText(page), 'a page with selectable text').toBe(true);

    const preExisting = await annotationIds(page, bookId!);
    expect(preExisting, 'the test book starts with no annotations').toHaveLength(0);
    const before = await paintCounts(page);
    const notesBefore = (await notesOnServer(page, bookId!)).length;
    await selectSomeText(page);
    await page.getByRole('button', { name: 'Green' }).click();

    // The colour tap must remain the fast path: a highlight with no note.
    await expect.poll(async () => (await paintCounts(page)).plain).toBe(before.plain + 1);
    const notes = await notesOnServer(page, bookId!);
    expect(notes.length).toBe(notesBefore + 1);
    expect(notes.filter((n: string | null) => !n).length).toBeGreaterThan(0);

    await restoreAnnotations(page, bookId!, preExisting);
  });
});

const drawer = (page: Page) => page.getByRole('navigation', { name: 'Highlights and notes' });

test.describe('reader highlights & notes drawer (#325)', () => {
  test.describe.configure({ mode: 'serial' });

  test('lists a highlight with its note, and jumps to it', async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const bookId = await openReaderOnEpub(page, testInfo.project.name === 'mobile' ? 3 : 2);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();
    expect(await pageUntilText(page), 'a page with selectable text').toBe(true);
    const preExisting = await annotationIds(page, bookId!);
    expect(preExisting, 'the test book starts with no annotations').toHaveLength(0);

    // Empty state is honest before anything exists.
    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    if (preExisting.length === 0) {
      await expect(drawer(page)).toContainText('No highlights yet');
    }
    await page.keyboard.press('Escape');

    // Make one, with a note.
    expect(await selectSomeText(page)).not.toBe('');
    await page.getByRole('button', { name: 'Add note' }).click();
    const NOTE = `Panel note ${Date.now()}`;
    await setNote(page, NOTE);
    await page.getByRole('button', { name: 'Save note' }).click();
    await expect.poll(async () => (await annotationIds(page, bookId!)).length)
      .toBe(preExisting.length + 1);

    // It appears in the drawer, with its note, without a reload.
    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    await expect(drawer(page)).toBeVisible();
    await expect(drawer(page)).toContainText(NOTE);
    const rows = drawer(page).locator('li');
    await expect(rows).toHaveCount(preExisting.length + 1);

    // Jumping closes the drawer and moves the book.
    await rows.last().locator('button').click();
    await expect(drawer(page)).toBeHidden();

    // Survives a reload — the drawer is populated from the server, not memory.
    await page.reload();
    await waitForReaderRender(page);
    await expect(page.getByRole('button', { name: 'Highlights and notes' })).toBeVisible();
    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    await expect(drawer(page)).toContainText(NOTE);

    await restoreAnnotations(page, bookId!, preExisting);
  });

  test('the drawer reflects a removed highlight without a reload', async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const bookId = await openReaderOnEpub(page, testInfo.project.name === 'mobile' ? 3 : 2);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();
    expect(await pageUntilText(page), 'a page with selectable text').toBe(true);
    const preExisting = await annotationIds(page, bookId!);
    expect(preExisting, 'the test book starts with no annotations').toHaveLength(0);

    await selectSomeText(page);
    await page.getByRole('button', { name: 'Yellow' }).click();
    await expect.poll(async () => (await annotationIds(page, bookId!)).length)
      .toBe(preExisting.length + 1);

    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    await expect(drawer(page).locator('li')).toHaveCount(preExisting.length + 1);
    await page.keyboard.press('Escape');

    // Delete it through the reader, then re-open the drawer.
    await page.evaluate(() => {
      const g = document.querySelector('.cwng-hl')!;
      const box = g.getBoundingClientRect();
      for (const type of ['mousedown', 'mouseup', 'click'])
        g.dispatchEvent(new MouseEvent(type, { bubbles: true, clientX: box.x + box.width / 2, clientY: box.y + box.height / 2 }));
    });
    await page.getByRole('button', { name: 'Remove highlight' }).click();
    await expect.poll(async () => (await annotationIds(page, bookId!)).length)
      .toBe(preExisting.length);

    await page.getByRole('button', { name: 'Highlights and notes' }).click();
    await expect(drawer(page).locator('li')).toHaveCount(preExisting.length);

    await restoreAnnotations(page, bookId!, preExisting);
  });
});

/*
 * Fullscreen. Asserts OUR wiring, not the browser's fullscreen implementation:
 * headless Chromium's real fullscreen is unreliable and it would be testing
 * Chrome rather than the reader. The Fullscreen API is stubbed so the test can
 * prove the button targets the reader shell and follows the browser's state.
 */
test.describe('reader fullscreen (#325)', () => {
  test.describe.configure({ mode: 'serial' });

  test('the control targets the reader shell and follows the browser state', async ({ page }, testInfo) => {
    test.setTimeout(120_000);
    const bookId = await openReaderOnEpub(page, testInfo.project.name === 'mobile' ? 1 : 0);
    expect(bookId, 'an EPUB that renders in the reader').not.toBeNull();

    // Record requests instead of entering real fullscreen.
    await page.evaluate(() => {
      (window as unknown as { __fsCalls: string[] }).__fsCalls = [];
      Element.prototype.requestFullscreen = function (this: Element) {
        (window as unknown as { __fsCalls: string[] }).__fsCalls.push(this.className);
        return Promise.resolve();
      };
    });

    const button = page.getByRole('button', { name: 'Full screen' });
    await expect(button).toBeVisible();
    await expect(button).toHaveAttribute('aria-pressed', 'false');
    await button.click();

    // It asked for fullscreen on the reader shell — not the viewer, not <body>.
    const calls = await page.evaluate(() => (window as unknown as { __fsCalls: string[] }).__fsCalls);
    expect(calls).toHaveLength(1);
    expect(calls[0]).toContain('reader');

    // The browser owns the state: until it reports fullscreen, the control must
    // not claim it. This is what breaks if someone "optimises" it to toggle its
    // own state optimistically — Escape would then leave the label lying.
    await expect(button).toHaveAttribute('aria-pressed', 'false');

    // Once the browser does report it, the control flips to the exit affordance.
    await page.evaluate(() => {
      Object.defineProperty(document, 'fullscreenElement', {
        configurable: true,
        get: () => document.querySelector('[class*="reader"]'),
      });
      document.dispatchEvent(new Event('fullscreenchange'));
    });
    const exitButton = page.getByRole('button', { name: 'Exit full screen' });
    await expect(exitButton).toBeVisible();
    await expect(exitButton).toHaveAttribute('aria-pressed', 'true');
  });
});

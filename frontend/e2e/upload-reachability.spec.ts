import { test, expect } from '@playwright/test';

/*
 * #1288 — "Upload button is missing or impossible to find" (new-UI feedback form,
 * i.e. someone switched back to the classic view over it).
 *
 * v4.1.11 moved Upload out of the sidebar into the Library toolbar to stop it
 * being "another identical sidebar row" (#664). That fix rendered it inside the
 * `!hideLibraryControls` gate — a gate whose stated purpose is hiding the
 * *view-scoped* controls (search box, Advanced link, read-status filter) on
 * entity and discovery views. Upload is a library-wide action, not a view
 * control, so it silently vanished on /hot, /discover, /rated, /archived,
 * /favorites and every author/series/tag/publisher/language/rating/format page,
 * leaving no upload affordance anywhere outside the plain Library route.
 *
 * Classic keeps its Upload button in the global navbar on every page
 * (cps/templates/layout.html), so this is a new-UI-only regression in reach.
 *
 * The contract pinned here:
 *   1. Upload stays reachable from entity-scoped and discovery catalog views.
 *   2. The account menu carries an Upload item on every page — the same
 *      "conventional place" fix #659/#720 applied to Admin, verified by
 *      account-menu-admin.spec.ts.
 * Both fail on the pre-fix build (red/green).
 *
 * The e2e seed user (admin / admin123, cwn-local) has role_upload, and the
 * fixture leaves "Enable Uploads" at its default (on), so the control is
 * expected present throughout.
 */

const UPLOAD_NAME = /upload books/i;

async function gotoAuthed(page: import('@playwright/test').Page, path: string) {
  await page.goto(path);
  // Authed shell rendered before asserting on absence/presence of chrome.
  await expect(page.locator('header').first()).toBeVisible({ timeout: 20_000 });
}

test.describe('#1288 — upload stays reachable outside the plain Library view', () => {
  test('discovery view (/hot) keeps an Upload affordance', async ({ page }) => {
    await gotoAuthed(page, '/app/hot');
    const upload = page.getByRole('link', { name: UPLOAD_NAME });
    await expect(upload, 'Upload is a library action, not a view-scoped control').toBeVisible();
    await expect(upload).toHaveAttribute('href', /\/upload$/);
  });

  test('entity-scoped view keeps an Upload affordance', async ({ page }) => {
    // Reach a real author page through the UI so the test never pins a seed id.
    // Authors (not tags) because every fixture book has one — a tag-based walk
    // silently skipped on the cwn-local seed, which is coverage theater.
    await gotoAuthed(page, '/app/authors');
    // Detail route is /authors/:id (plural), per lib/routes.ts — the list page
    // itself is /authors with no trailing slash, so this matches only entries.
    const firstAuthor = page.locator('a[href*="/authors/"]').first();
    await expect(firstAuthor).toBeVisible({ timeout: 20_000 });
    await firstAuthor.click();
    await expect(page).toHaveURL(/\/authors\/[^/]+$/);

    const upload = page.getByRole('link', { name: UPLOAD_NAME });
    await expect(upload).toBeVisible();
    await expect(upload).toHaveAttribute('href', /\/upload$/);
  });

  test('the Upload control actually lands on the upload page', async ({ page }) => {
    await gotoAuthed(page, '/app/hot');
    await page.getByRole('link', { name: UPLOAD_NAME }).first().click();
    await expect(page).toHaveURL(/\/upload(\/|$|\?)/);
    // Not a dead link — the dropzone's file input is really there.
    await expect(page.getByLabel('Choose books to upload')).toBeAttached();
  });
});

test.describe('#1288 — account menu carries Upload on every page (#659/#720 shape)', () => {
  async function openAccountMenu(page: import('@playwright/test').Page, path: string) {
    await gotoAuthed(page, path);
    const trigger = page.getByRole('button', { name: /account:/i });
    await expect(trigger).toBeVisible();
    await trigger.click();
    // Scope to the menu wrapper so we never match the Library toolbar button.
    return trigger.locator('xpath=ancestor::div[1]');
  }

  test('desktop: Upload is in the account menu from a book page', async ({ page }) => {
    // A book detail page has no catalog toolbar at all — the account menu is the
    // only upload path there, which is exactly the reported dead end.
    await gotoAuthed(page, '/app');
    const firstBook = page.locator('a[href*="/book/"]').first();
    await expect(firstBook).toBeVisible({ timeout: 20_000 });
    const href = await firstBook.getAttribute('href');

    const menu = await openAccountMenu(page, href!.replace(/^.*\/app/, '/app'));
    const upload = menu.getByRole('link', { name: UPLOAD_NAME });
    await expect(upload, 'account menu exposes Upload for users who may upload').toBeVisible();
    await expect(upload).toHaveAttribute('href', /\/upload$/);

    await upload.click();
    await expect(page).toHaveURL(/\/upload(\/|$|\?)/);
  });

  test('mobile: Upload is in the account menu too', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', 'mobile viewport project only');
    const menu = await openAccountMenu(page, '/app/hot');
    const upload = menu.getByRole('link', { name: UPLOAD_NAME });
    await expect(upload).toBeVisible();
    await expect(upload).toHaveAttribute('href', /\/upload$/);
  });
});

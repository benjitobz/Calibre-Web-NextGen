import { test, expect } from '@playwright/test';

/*
 * #1112 — the Edit pencil overlapped the "Read now" label on a book card.
 *
 * Reported by @Andrew-H2O after the vertical-alignment fix on #863, with a
 * 335px-wide screenshot: the label read "Read no…" because the pencil sat on
 * top of its tail. This is a TOUCH default, not a hover edge case — the
 * coarse-pointer block in BookCard.module.css sets both the label and the
 * pencil to opacity 1, so on a phone they are permanently visible together.
 *
 * Measured on a real library before the fix: 52px of the label ran under the
 * button at 360px and at 768px. After: 0px.
 *
 * Geometry rather than a screenshot, because the failure is positional and a
 * pixel diff would also fire on every unrelated restyle.
 */

const OVERLAP = `() => {
  const labels = [...document.querySelectorAll('*')].filter(
    (e) => typeof e.className === 'string' && /readNow/.test(e.className));
  let checked = 0, worst = 0;
  for (const label of labels) {
    const card = label.closest('[class*="wrap"]');
    if (!card) continue;
    const pencil = card.querySelector('[class*="quickEditBtn"]');
    if (!pencil || getComputedStyle(pencil).opacity === '0') continue;
    checked++;
    const lb = label.getBoundingClientRect();
    const pb = pencil.getBoundingClientRect();
    // Content box: padding is the reserved corner, so exclude it.
    const contentRight = lb.right - parseFloat(getComputedStyle(label).paddingRight);
    worst = Math.max(worst, Math.round(contentRight - pb.left));
  }
  return { checked, worst };
}`;

for (const [name, width, height] of [['phone', 360, 760], ['tablet', 768, 1024]] as const) {
  test(`the Edit control never covers the "Read now" label (${name}, #1112)`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    await page.goto('/app/');
    await page.waitForLoadState('networkidle');

    const result = await page.evaluate(OVERLAP) as { checked: number; worst: number };
    test.skip(result.checked === 0, 'no card in this seed shows both a read link and an edit control');

    expect(result.worst,
      `the "Read now" label runs ${result.worst}px under the Edit control, clipping its tail (#1112)`
    ).toBeLessThanOrEqual(0);
  });
}

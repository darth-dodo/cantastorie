import { test, expect } from '@playwright/test';

// /play is the only shell that loads both tokens.css and palette.js.
// Single palette: indigo (orchid accent #A88BE0 across light + dusk).
const THEMES = ['light', 'dusk'];

for (const theme of THEMES) {
  test(`accent is orchid for indigo/${theme}`, async ({ page }) => {
    await page.goto(`/play?palette=indigo&theme=${theme}`);
    const probe = await page.evaluate(() => {
      const d = document.createElement('div');
      d.style.color = getComputedStyle(document.documentElement).getPropertyValue('--accent');
      document.body.appendChild(d);
      return getComputedStyle(d).color;
    });
    expect(probe).toBe('rgb(168, 139, 224)'); // #A88BE0
  });
}

test('indigo light — --rest and --primary are distinct', async ({ page }) => {
  await page.goto('/play?palette=indigo&theme=light');
  const [rest, primary] = await page.evaluate(() => {
    const s = getComputedStyle(document.documentElement);
    return [s.getPropertyValue('--rest').trim(), s.getPropertyValue('--primary').trim()];
  });
  expect(rest).not.toBe(primary);
});

test('indigo dusk — --rest and --primary are distinct', async ({ page }) => {
  await page.goto('/play?palette=indigo&theme=dusk');
  const [rest, primary] = await page.evaluate(() => {
    const s = getComputedStyle(document.documentElement);
    return [s.getPropertyValue('--rest').trim(), s.getPropertyValue('--primary').trim()];
  });
  expect(rest).not.toBe(primary);
});

test("dusk shelf is painted from the indigo tokens, not the retired warm browns", async ({ page }) => {
  await page.goto("/play?lang=it&theme=dusk");
  await page.waitForSelector(".shelf .cover");
  const { bg, fade, caption } = await page.evaluate(() => {
    const shelf = document.querySelector(".shelf");
    return {
      bg: getComputedStyle(shelf).backgroundImage + " " + getComputedStyle(shelf).backgroundColor,
      fade: getComputedStyle(shelf, "::after").backgroundImage,
      caption: getComputedStyle(document.querySelector(".cover-caption")).color,
    };
  });
  expect(bg).toContain("rgb(28, 33, 48)"); // --surface dusk #1C2130
  expect(fade).toContain("rgb(28, 33, 48)");
  expect(caption).toBe("rgb(151, 160, 181)"); // --ink-soft dusk #97A0B5
});

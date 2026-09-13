import { test, expect } from '@playwright/test';

// /play is the only shell that loads both tokens.css and palette.js.
// We drive it with ?palette=…&theme=… which palette.js already supports.
const PALETTES = ['indigo', 'warm', 'seaglass', 'plum'];
const THEMES = ['light', 'dusk'];

for (const palette of PALETTES) {
  for (const theme of THEMES) {
    test(`accent is orchid for ${palette}/${theme}`, async ({ page }) => {
      await page.goto(`/play?palette=${palette}&theme=${theme}`);
      const probe = await page.evaluate(() => {
        const d = document.createElement('div');
        d.style.color = getComputedStyle(document.documentElement).getPropertyValue('--accent');
        document.body.appendChild(d);
        return getComputedStyle(d).color;
      });
      expect(probe).toBe('rgb(168, 139, 224)'); // #A88BE0
    });
  }
}

for (const theme of ['light', 'dusk']) {
  test(`warm --rest differs from --primary (${theme})`, async ({ page }) => {
    await page.goto(`/play?palette=warm&theme=${theme}`);
    const [rest, primary] = await page.evaluate(() => {
      const s = getComputedStyle(document.documentElement);
      return [s.getPropertyValue('--rest').trim(), s.getPropertyValue('--primary').trim()];
    });
    expect(rest).not.toBe(primary);
  });
}

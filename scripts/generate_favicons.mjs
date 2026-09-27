// Rasterize src/static/icons/favicon.svg into the PNG and ICO icons (AI-467).
//
//   node scripts/generate_favicons.mjs
//
// Renders with the Playwright Chromium the e2e suite already installs, so no
// image tooling is needed. Outputs, next to the SVG:
//   favicon.ico          16, 32 and 48 px frames (PNG-in-ICO)
//   apple-touch-icon.png 180 px, square tile (iOS rounds the corners itself)

import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "@playwright/test";

const ICONS = join(dirname(fileURLToPath(import.meta.url)), "..", "src", "static", "icons");
const svg = readFileSync(join(ICONS, "favicon.svg"), "utf8");
// Home-screen icons get a full-bleed square: the platform applies its own mask.
const squareSvg = svg.replace('rx="14"', 'rx="0"');

const browser = await chromium.launch();
const page = await browser.newPage();

async function render(source, size) {
  await page.setViewportSize({ width: size, height: size });
  const uri = `data:image/svg+xml;base64,${Buffer.from(source).toString("base64")}`;
  await page.setContent(
    `<style>html,body{margin:0;background:transparent}</style>` +
      `<img src="${uri}" width="${size}" height="${size}" style="display:block">`,
  );
  await page.locator("img").evaluate((img) => img.decode());
  return page.screenshot({ omitBackground: true, clip: { x: 0, y: 0, width: size, height: size } });
}

// ICO: a 6-byte header, one 16-byte entry per frame, then the PNG frames.
function ico(frames) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); // reserved
  header.writeUInt16LE(1, 2); // type: icon
  header.writeUInt16LE(frames.length, 4);
  let offset = 6 + 16 * frames.length;
  const entries = frames.map(({ size, png }) => {
    const entry = Buffer.alloc(16);
    entry.writeUInt8(size >= 256 ? 0 : size, 0); // width
    entry.writeUInt8(size >= 256 ? 0 : size, 1); // height
    entry.writeUInt8(0, 2); // palette colours
    entry.writeUInt8(0, 3); // reserved
    entry.writeUInt16LE(1, 4); // colour planes
    entry.writeUInt16LE(32, 6); // bits per pixel
    entry.writeUInt32LE(png.length, 8);
    entry.writeUInt32LE(offset, 12);
    offset += png.length;
    return entry;
  });
  return Buffer.concat([header, ...entries, ...frames.map((f) => f.png)]);
}

const frames = [];
for (const size of [16, 32, 48]) frames.push({ size, png: await render(svg, size) });
writeFileSync(join(ICONS, "favicon.ico"), ico(frames));
writeFileSync(join(ICONS, "apple-touch-icon.png"), await render(squareSvg, 180));

await browser.close();
console.log(`wrote favicon.ico (16/32/48) and apple-touch-icon.png (180) to ${ICONS}`);

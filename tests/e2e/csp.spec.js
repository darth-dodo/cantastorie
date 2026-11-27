// Content-Security-Policy, enforced (AI-505): the public pages render and play
// with the policy the server sends, and the browser reports no violation.
// Violations are collected from the page's securitypolicyviolation events and
// from console messages, so a blocked inline script or style fails the test.

import { expect, test } from "@playwright/test";

const STORY_TITLE = "La barchetta e la luna";

async function watchViolations(page) {
  const consoleErrors = [];
  page.on("console", (message) => {
    const text = message.text();
    if (/content security policy|refused to (load|apply|execute|connect)/i.test(text)) {
      consoleErrors.push(text);
    }
  });
  await page.addInitScript(() => {
    window.__cspViolations = [];
    document.addEventListener("securitypolicyviolation", (event) => {
      window.__cspViolations.push(`${event.violatedDirective} ${event.blockedURI}`);
    });
  });
  return async () => {
    const events = await page.evaluate(() => window.__cspViolations ?? []);
    return [...events, ...consoleErrors];
  };
}

async function expectEnforced(response) {
  const headers = response.headers();
  expect(headers["content-security-policy"]).toContain("default-src 'self'");
  expect(headers["content-security-policy-report-only"]).toBeUndefined();
}

test("the landing page renders under the policy with no violation", async ({ page }) => {
  const violations = await watchViolations(page);
  const response = await page.goto("/?theme=light");
  await expectEnforced(response);

  // The scroll reveal (now landing.js) runs: steps become visible on scroll.
  const steps = page.locator(".l-step-reveal");
  await steps.last().scrollIntoViewIfNeeded();
  await expect(steps.last()).toHaveClass(/l-step-reveal--visible/);

  // The step illustrations (now landing.css classes) still paint.
  const art = await page
    .locator(".l-step-art--taps")
    .evaluate((el) => getComputedStyle(el).backgroundImage);
  expect(art).toContain("/static/img/landing-spot-taps.png");

  expect(await violations()).toEqual([]);
});

test("the child player: shelf, story, a page turn with audio, no violation", async ({ page }) => {
  const violations = await watchViolations(page);
  const response = await page.goto("/play?lang=it&theme=dusk");
  await expectEnforced(response);

  await expect(page.locator(".shelf .cover").first()).toBeVisible();
  await page.locator(".greeting").click();
  await page.waitForFunction(() => window.__shell?.engine.unlocked === true);
  await page.getByRole("button", { name: STORY_TITLE }).click();
  await expect(page.locator(".player")).toBeVisible();
  await expect(page.locator(".page-wash.current")).toHaveAttribute("data-page", "0");
  await expect(page.locator(".page-wash.current")).toHaveAttribute("data-page", "1", {
    timeout: 10_000,
  });

  expect(await violations()).toEqual([]);
});

test("the offline screen shows under the policy with no violation", async ({ page }) => {
  const violations = await watchViolations(page);
  await page.route("**/manifest.json", (route) => route.abort());
  await page.goto("/play?theme=dusk");
  await expect(page.locator(".offline")).toBeVisible();
  await page.locator(".offline").click();
  await expect(page.locator(".offline")).toBeVisible();

  expect(await violations()).toEqual([]);
});

test("the 404 page renders its own stylesheet with no violation", async ({ page }) => {
  const violations = await watchViolations(page);
  const response = await page.goto("/no-such-page?theme=light");
  expect(response.status()).toBe(404);
  await expectEnforced(response);

  // The moved 404 styles apply: the page is a centred flex column.
  const layout = await page
    .locator(".not-found")
    .evaluate((el) => getComputedStyle(el).display);
  expect(layout).toBe("flex");

  expect(await violations()).toEqual([]);
});

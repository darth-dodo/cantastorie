import { defineConfig, devices } from "@playwright/test";

// The dev server port is overridable so the e2e suite can run against a
// worktree's own server without colliding with a main-checkout server already
// holding 8000. Defaults to 8000 for CI and local runs.
const PORT = process.env.E2E_PORT ?? "8000";

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    viewport: { width: 402, height: 874 },
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: `uv run uvicorn src.api.main:app --host 127.0.0.1 --port ${PORT}`,
    url: `http://127.0.0.1:${PORT}`,
    // Pin ASSET_BASE to the same-origin static mount so the suite reads the
    // local dev fixtures (dev-branching et al.) deterministically — never a
    // developer's .env pointing the shell at the R2 bucket, which holds only
    // published production content and none of these fixtures.
    env: { ...process.env, ASSET_BASE: "/static/content" },
    reuseExistingServer: true,
    timeout: 30_000,
  },
});

import { defineConfig } from "@playwright/test";

const repositoryRoot = "..";
const port = 8765;

export default defineConfig({
  testDir: "../tests/e2e/money_tracker/web",
  testIgnore: "installed-service.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  outputDir: "test-results/playwright-artifacts",
  reporter: process.env.CI
    ? [["line"], ["junit", { outputFile: "test-results/playwright-junit.xml" }]]
    : [["line"]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    browserName: "chromium",
    headless: true,
    launchOptions: {
      executablePath:
        process.env.MONEY_TRACKER_E2E_BROWSER ??
        (process.env.CI ? undefined : "/usr/bin/google-chrome"),
    },
    // Video needs Playwright's ffmpeg, which CI installs with its bundled
    // Chromium; a local run with the system browser keeps the trace only.
    video: process.env.CI ? "retain-on-failure" : "off",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  webServer: {
    command:
      "uv run --env-file .env python tests/e2e/money_tracker/web/runtime.py serve",
    cwd: repositoryRoot,
    url: `http://127.0.0.1:${port}/api/v1/health/ready`,
    reuseExistingServer: false,
    timeout: 60_000,
  },
  globalTeardown: "../tests/e2e/money_tracker/web/teardown.ts",
});

import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "../tests/e2e/money_tracker/web",
  testMatch: "installed-service.spec.ts",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: [["line"]],
  use: {
    baseURL: "http://127.0.0.1:8000",
    browserName: "chromium",
    headless: true,
    launchOptions: {
      executablePath:
        process.env.MONEY_TRACKER_E2E_BROWSER ?? "/usr/bin/google-chrome",
    },
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
});

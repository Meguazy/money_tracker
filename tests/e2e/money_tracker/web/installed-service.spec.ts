import { execFileSync } from "node:child_process";

import { expect, test } from "@playwright/test";

const directRoutes = [
  "/",
  "/analisi",
  "/consigli",
  "/movimenti",
  "/categorie",
  "/fonti",
  "/budget",
  "/scontrini",
  "/messaggistica",
  "/impostazioni",
];

test.describe.configure({ mode: "serial" });

test("serves health, compiled assets, and every direct route from the installed unit", async ({
  page,
  request,
}) => {
  expect(
    execFileSync("systemctl", ["--user", "is-active", "money-tracker-api.service"], {
      encoding: "utf8",
    }).trim(),
  ).toBe("active");

  const live = await request.get("/api/v1/health/live");
  const ready = await request.get("/api/v1/health/ready");
  expect(live.ok()).toBeTruthy();
  expect(ready.ok()).toBeTruthy();
  expect(await live.json()).toEqual({ status: "healthy" });
  expect(await ready.json()).toEqual({ status: "healthy" });

  for (const path of directRoutes) {
    const response = await page.goto(path);
    expect(response?.ok()).toBeTruthy();
    await expect(page.getByRole("navigation", { name: "Navigazione principale" })).toBeVisible();
    await page.reload();
    await expect(page.locator("main#main-content")).toBeVisible();
  }

  await page.goto("/");
  const assetPath = await page.locator('script[type="module"]').getAttribute("src");
  expect(assetPath).toMatch(/^\/assets\/.*\.js$/);
  const asset = await request.get(assetPath as string);
  expect(asset.ok()).toBeTruthy();
  expect(asset.headers()["content-type"]).toContain("javascript");
});

test("enforces Host, Origin, CORS, and CSRF at the installed boundary", async ({
  request,
}) => {
  const disallowedHost = await request.get("/api/v1/health/live", {
    headers: { Host: "money-tracker.invalid" },
  });
  expect(disallowedHost.status()).toBe(403);

  const disallowedOrigin = await request.get("/api/v1/health/live", {
    headers: { Origin: "http://money-tracker.invalid" },
  });
  expect(disallowedOrigin.status()).toBe(403);
  expect(disallowedOrigin.headers()["access-control-allow-origin"]).toBeUndefined();

  const missingCsrf = await request.post("/api/v1/advice", {
    headers: { Origin: "http://127.0.0.1:8000" },
    data: { message: "Come vanno i budget?" },
  });
  expect(missingCsrf.status()).toBe(403);
});

test("returns to browser readiness after a real systemd restart", async ({ page }) => {
  await page.goto("/analisi");
  await expect(page.getByRole("heading", { name: "Guarda la spesa nel tempo" })).toBeVisible();

  const mainPid = () =>
    execFileSync(
      "systemctl",
      ["--user", "show", "money-tracker-api.service", "--property=MainPID", "--value"],
      { encoding: "utf8" },
    ).trim();
  const pidBeforeRestart = mainPid();
  expect(pidBeforeRestart).not.toBe("0");

  execFileSync("systemctl", ["--user", "restart", "money-tracker-api.service"]);

  await expect
    .poll(
      () =>
        execFileSync(
          "systemctl",
          ["--user", "is-active", "money-tracker-api.service"],
          { encoding: "utf8" },
        ).trim(),
      { timeout: 30_000 },
    )
    .toBe("active");
  const pidAfterRestart = mainPid();
  expect(pidAfterRestart).not.toBe("0");
  expect(pidAfterRestart).not.toBe(pidBeforeRestart);
  // Type=simple reports "active" before uvicorn listens, so a refused
  // connection means "not ready yet"; HTTP readiness is the real signal.
  await expect
    .poll(
      async () => {
        try {
          const response = await page.request.get("/api/v1/health/ready");
          return response.status();
        } catch {
          return null;
        }
      },
      { timeout: 30_000 },
    )
    .toBe(200);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Guarda la spesa nel tempo" })).toBeVisible();
});

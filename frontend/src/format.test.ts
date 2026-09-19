import { afterEach, describe, expect, it, vi } from "vitest";

import { romeDateTimeInput, romeDateTimeInputToInstant } from "./format";

// Browser zones on both sides of Europe/Rome, and Rome itself. Node applies a
// stubbed TZ immediately, so each case runs as if the browser were there.
const browserZones = ["UTC", "America/Los_Angeles", "Asia/Tokyo", "Europe/Rome"];

afterEach(() => {
  vi.unstubAllEnvs();
});

describe("Rome date-time inputs", () => {
  it.each(browserZones)("read wall time as Europe/Rome in a %s browser", (zone) => {
    vi.stubEnv("TZ", zone);

    // The value the first UTC CI run sent two hours into the future.
    expect(romeDateTimeInputToInstant("2026-09-19T01:48")?.toISOString()).toBe(
      "2026-09-18T23:48:00.000Z",
    );
    expect(romeDateTimeInputToInstant("2026-01-15T09:00")?.toISOString()).toBe(
      "2026-01-15T08:00:00.000Z",
    );
    expect(romeDateTimeInput(new Date("2026-09-18T23:48:00Z"))).toBe("2026-09-19T01:48");
    expect(romeDateTimeInput(new Date("2026-12-31T23:30:00Z"))).toBe("2027-01-01T00:30");
  });

  it("resolves the repeated autumn hour to its summer occurrence", () => {
    vi.stubEnv("TZ", "UTC");

    expect(romeDateTimeInputToInstant("2026-10-25T02:30")?.toISOString()).toBe(
      "2026-10-25T00:30:00.000Z",
    );
    expect(romeDateTimeInput(new Date("2026-10-25T01:30:00Z"))).toBe("2026-10-25T02:30");
  });

  it("moves a skipped spring wall time forward by the skipped hour", () => {
    vi.stubEnv("TZ", "UTC");

    expect(romeDateTimeInputToInstant("2026-03-29T02:30")?.toISOString()).toBe(
      "2026-03-29T01:30:00.000Z",
    );
    expect(romeDateTimeInput(new Date("2026-03-29T01:30:00Z"))).toBe("2026-03-29T03:30");
  });

  it.each([
    "",
    "2026-02-30T10:00",
    "2026-09-19T24:00",
    "2026-09-19 01:48",
    "2026-09-19T01:48:00",
  ])("rejects %j", (value) => {
    expect(romeDateTimeInputToInstant(value)).toBeNull();
  });

  it("round-trips every minute of a year except the repeated autumn hour", () => {
    vi.stubEnv("TZ", "America/Los_Angeles");
    const start = Date.UTC(2026, 0, 1);
    const end = Date.UTC(2027, 0, 1);
    const hour = 60 * 60 * 1000;

    for (let instant = start; instant < end; instant += 97 * 60 * 1000) {
      const wall = romeDateTimeInput(new Date(instant));
      const recovered = romeDateTimeInputToInstant(wall)?.getTime();
      const repeated = wall.startsWith("2026-10-25T02:") && recovered === instant - hour;
      expect(recovered === instant || repeated, wall).toBe(true);
    }
  });
});

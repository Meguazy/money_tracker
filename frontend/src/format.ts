import type { Money } from "./api";

const eur = new Intl.NumberFormat("it-IT", {
  style: "currency",
  currency: "EUR",
});

const occurredAt = new Intl.DateTimeFormat("it-IT", {
  dateStyle: "medium",
  timeStyle: "short",
  timeZone: "Europe/Rome",
});

export function parseEuroToMinorUnits(value: string): number | null {
  const match = /^(-?)([0-9]+)(?:[,.]([0-9]{1,2}))?$/.exec(value.trim());
  if (match === null) {
    return null;
  }
  const whole = Number(match[2]);
  const cents = Number((match[3] ?? "").padEnd(2, "0"));
  const absolute = whole * 100 + cents;
  const result = match[1] === "-" ? -absolute : absolute;
  return Number.isSafeInteger(result) && result !== 0 ? result : null;
}

function exactEuroDigits(minorUnits: bigint): string {
  const sign = minorUnits < 0n ? "-" : "";
  const absolute = minorUnits < 0n ? -minorUnits : minorUnits;
  const digits = absolute.toString().padStart(3, "0");
  return `${sign}${digits.slice(0, -2)},${digits.slice(-2)}`;
}

export function minorUnitsToEuroInput(minorUnits: number): string {
  if (!Number.isSafeInteger(minorUnits)) {
    throw new Error("L'importo non è rappresentabile in centesimi esatti.");
  }
  return exactEuroDigits(BigInt(minorUnits));
}

export function formatExactEuro(minorUnits: bigint): string {
  return `${exactEuroDigits(minorUnits)} €`;
}

export function formatOccurredAt(value: string): string {
  return occurredAt.format(new Date(value));
}

export function formatMoney(value: Money): string {
  return eur.format(value.minor_units / 100);
}

export function monthLabel(period: string): string {
  const [year, month] = period.split("-").map(Number);
  return new Intl.DateTimeFormat("it-IT", {
    month: "long",
    year: "numeric",
    timeZone: "Europe/Rome",
  }).format(new Date(Date.UTC(year, month - 1, 1)));
}

export function currentRomeMonth(now: Date = new Date()): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    timeZone: "Europe/Rome",
  }).formatToParts(now);
  const year = parts.find((part) => part.type === "year")?.value;
  const month = parts.find((part) => part.type === "month")?.value;
  if (year === undefined || month === undefined) {
    throw new Error("Il calendario locale non è disponibile.");
  }
  return `${year}-${month}`;
}

export function currentRomeDate(now: Date = new Date()): string {
  const values = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      timeZone: "Europe/Rome",
    })
      .formatToParts(now)
      .map((part) => [part.type, part.value]),
  );
  if (values.year === undefined || values.month === undefined || values.day === undefined) {
    throw new Error("Il calendario locale non è disponibile.");
  }
  return `${values.year}-${values.month}-${values.day}`;
}

const romeWallClock = new Intl.DateTimeFormat("en-CA", {
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
  timeZone: "Europe/Rome",
});

const DAY_MS = 24 * 60 * 60 * 1000;

function romeWallClockParts(instant: Date): Record<string, string> {
  return Object.fromEntries(
    romeWallClock.formatToParts(instant).map((part) => [part.type, part.value]),
  );
}

// Milliseconds by which Europe/Rome wall time is ahead of UTC at an instant.
function romeOffsetMs(epochMs: number): number {
  const values = romeWallClockParts(new Date(epochMs));
  const wall = Date.UTC(
    Number(values.year),
    Number(values.month) - 1,
    Number(values.day),
    Number(values.hour),
    Number(values.minute),
    Number(values.second),
  );
  return wall - Math.floor(epochMs / 1000) * 1000;
}

/**
 * Formats an instant as Europe/Rome wall time for a `datetime-local` input.
 *
 * The dashboard's owner lives in Europe/Rome, so date-time fields show and
 * accept Rome wall time whatever time zone the browser runs in.
 */
export function romeDateTimeInput(instant: Date = new Date()): string {
  const values = romeWallClockParts(instant);
  return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`;
}

/**
 * Converts a `datetime-local` value, read as Europe/Rome wall time, to the
 * instant it names, independently of the browser's own time zone.
 *
 * A wall time repeated when summer time ends resolves to its first, summer
 * occurrence. A wall time skipped when summer time starts moves forward by the
 * skipped hour, as Temporal's "compatible" disambiguation does.
 *
 * @returns The instant, or null when the value is not a valid date and time.
 */
export function romeDateTimeInputToInstant(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(value);
  if (match === null) {
    return null;
  }
  const [year, month, day, hour, minute] = match.slice(1).map(Number);
  const wall = Date.UTC(year, month - 1, day, hour, minute);
  const normalized = new Date(wall);
  if (
    normalized.getUTCFullYear() !== year ||
    normalized.getUTCMonth() !== month - 1 ||
    normalized.getUTCDate() !== day ||
    normalized.getUTCHours() !== hour ||
    normalized.getUTCMinutes() !== minute
  ) {
    return null;
  }
  // Rome's offset can differ only across a transition near the wall time.
  const offsetBefore = romeOffsetMs(wall - DAY_MS);
  const offsetAfter = romeOffsetMs(wall + DAY_MS);
  const matching = [...new Set([offsetBefore, offsetAfter])]
    .map((offset) => wall - offset)
    .filter((instant) => instant + romeOffsetMs(instant) === wall)
    .sort((left, right) => left - right);
  return new Date(matching[0] ?? wall - offsetBefore);
}

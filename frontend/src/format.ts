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

export function currentLocalDateTime(now: Date = new Date()): string {
  const values = Object.fromEntries(
    new Intl.DateTimeFormat("en-CA", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
      timeZone: "Europe/Rome",
    })
      .formatToParts(now)
      .map((part) => [part.type, part.value]),
  );
  return `${values.year}-${values.month}-${values.day}T${values.hour}:${values.minute}`;
}

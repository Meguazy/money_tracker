import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { RangeOverview, RangeSelection } from "./api";
import { AnalyticsPage } from "./AnalyticsPage";

const month: RangeOverview = {
  period: {
    granularity: "month",
    start: "2026-09-01",
    end: "2026-09-30",
    day_count: 30,
    iso_year: null,
    iso_week: null,
  },
  posted_total: { minor_units: 5000, currency: "EUR" },
  funding_split: {
    personal_funds: { minor_units: 2000, currency: "EUR" },
    benefit: { minor_units: 3000, currency: "EUR" },
    other: { minor_units: 0, currency: "EUR" },
  },
  funding_sources: [
    {
      name: "Carta",
      funding_class: "personal_funds",
      amount: { minor_units: 2000, currency: "EUR" },
    },
    {
      name: "Buoni pasto",
      funding_class: "benefit",
      amount: { minor_units: 3000, currency: "EUR" },
    },
  ],
  categories: [
    {
      category_id: "018cf061-39d8-7c10-9b1f-319cedc93ef2",
      category_name: "Spesa alimentare",
      parent_name: "Alimentazione",
      amount: { minor_units: 5000, currency: "EUR" },
    },
  ],
  merchants: [
    {
      merchant: "Mercato Centrale",
      amount: { minor_units: 5000, currency: "EUR" },
      movement_count: 1,
    },
    {
      merchant: null,
      amount: { minor_units: 0, currency: "EUR" },
      movement_count: 1,
    },
  ],
  movement_count: 2,
  comparison: {
    current_period: {
      granularity: "month",
      start: "2026-09-01",
      end: "2026-09-30",
      day_count: 30,
      iso_year: null,
      iso_week: null,
    },
    previous_period: {
      granularity: "month",
      start: "2026-08-01",
      end: "2026-08-31",
      day_count: 31,
      iso_year: null,
      iso_week: null,
    },
    current_total: { minor_units: 5000, currency: "EUR" },
    previous_total: { minor_units: 4000, currency: "EUR" },
    delta: { minor_units: 1000, currency: "EUR" },
    percentage_change_basis_points: 2500,
    percentage_change_status: "defined",
  },
  budgets: {
    period: { year: 2026, month: 9 },
    positions: [
      {
        category_name: "Spesa alimentare",
        target: { minor_units: 8000, currency: "EUR" },
        posted_spend: { minor_units: 5000, currency: "EUR" },
        remaining: { minor_units: 3000, currency: "EUR" },
      },
    ],
    unbudgeted_categories: ["Abbonamenti"],
  },
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function renderPage(
  loader: (selection: RangeSelection) => Promise<RangeOverview>,
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <AnalyticsPage loader={loader} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

async function selectGranularity(
  user: ReturnType<typeof userEvent.setup>,
  label: string,
) {
  const input = screen.getByRole("textbox", {
    name: "Periodo da osservare",
  });
  await user.click(input);
  const listboxId = input.getAttribute("aria-controls");
  if (listboxId === null) {
    throw new Error("Il selettore non espone la propria lista.");
  }
  await user.click(
    within(document.getElementById(listboxId)!).getByRole("option", {
      name: label,
      hidden: true,
    }),
  );
}

describe("AnalyticsPage", () => {
  it("shows exact purpose, funding and monthly budget facts in tables", async () => {
    renderPage(async () => month);

    expect(
      await screen.findByRole("heading", { name: "settembre 2026" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Alimentazione / Spesa alimentare"),
    ).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "Carta" })).toBeInTheDocument();
    expect(screen.getAllByText(/50,00\s€/).length).toBeGreaterThanOrEqual(3);
    expect(screen.getAllByText(/30,00\s€/).length).toBeGreaterThan(0);
    expect(screen.getByText("Abbonamenti")).toBeInTheDocument();
    expect(
      screen.getByRole("table", { name: /senza sommare genitori e figli/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("table", { name: /non sono saldi/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("rowheader", { name: "Mercato Centrale" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("rowheader", { name: "Esercente non indicato" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("table", { name: /alternativa tabellare esatta al grafico/i }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("table", {
        name: /valori assoluti, delta monetario e variazione percentuale/i,
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("+25,00%")).toBeInTheDocument();
    const merchantChart = await screen.findByRole("img", {
      name: "Grafico della spesa netta per esercente",
    });
    const comparisonChart = await screen.findByRole("img", {
      name: "Grafico del confronto tra periodo corrente e precedente",
    });
    expect(merchantChart).toHaveAttribute("tabindex", "0");
    expect(comparisonChart).toHaveAttribute("tabindex", "0");
  });

  it("submits an ISO week selected by one of its days", async () => {
    const user = userEvent.setup();
    const loader = vi.fn(async (selection: RangeSelection) => ({
      ...month,
      period: {
        granularity: selection.granularity,
        start: "2025-12-29",
        end: "2026-01-04",
        day_count: 7,
        iso_year: 2026,
        iso_week: 1,
      },
      budgets: null,
    }));
    renderPage(loader);
    await vi.waitFor(() => expect(loader).toHaveBeenCalledTimes(1));

    await selectGranularity(user, "Settimana ISO");
    const weekDay = screen.getByLabelText("Un giorno della settimana");
    await user.clear(weekDay);
    await user.type(weekDay, "2025-12-31");
    await user.click(
      screen.getByRole("button", { name: "Aggiorna analisi" }),
    );

    expect(
      await screen.findByRole("heading", {
        name: "Settimana ISO 1 del 2026",
      }),
    ).toBeInTheDocument();
    expect(loader).toHaveBeenLastCalledWith(
      { granularity: "week", day: "2025-12-31" },
      expect.any(AbortSignal),
    );
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Budget non confrontati",
    );
  });

  it("submits both inclusive ends of a custom interval", async () => {
    const user = userEvent.setup();
    const loader = vi.fn(async () => month);
    renderPage(loader);
    await screen.findByRole("heading", { name: "settembre 2026" });

    await selectGranularity(user, "Intervallo personalizzato");
    const start = screen.getByLabelText("Dal giorno");
    const end = screen.getByLabelText("Al giorno");
    await user.clear(start);
    await user.type(start, "2026-08-01");
    await user.clear(end);
    await user.type(end, "2026-09-13");
    await user.click(
      screen.getByRole("button", { name: "Aggiorna analisi" }),
    );

    expect(loader).toHaveBeenLastCalledWith(
      {
        granularity: "custom",
        start: "2026-08-01",
        end: "2026-09-13",
      },
      expect.any(AbortSignal),
    );
  });

  it("states an empty period without inventing category rows", async () => {
    renderPage(async () => ({
      ...month,
      posted_total: { minor_units: 0, currency: "EUR" },
      funding_split: {
        personal_funds: { minor_units: 0, currency: "EUR" },
        benefit: { minor_units: 0, currency: "EUR" },
        other: { minor_units: 0, currency: "EUR" },
      },
      categories: [],
      movement_count: 0,
      budgets: null,
    }));

    expect(
      await screen.findByText("Nessun movimento registrato in questo periodo."),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Spesa per finalità" }),
    ).not.toBeInTheDocument();
  });

  it("states why percentage change is undefined with a zero baseline", async () => {
    renderPage(async () => ({
      ...month,
      comparison: {
        ...month.comparison,
        previous_total: { minor_units: 0, currency: "EUR" },
        delta: { minor_units: 5000, currency: "EUR" },
        percentage_change_basis_points: null,
        percentage_change_status: "previous_zero",
      },
    }));

    expect(
      await screen.findByText(
        "Non definita: il periodo precedente è pari a zero.",
      ),
    ).toBeInTheDocument();
  });

  it("exposes an actionable error state to keyboard users", async () => {
    const user = userEvent.setup();
    const loader = vi
      .fn()
      .mockRejectedValue(new Error("Database non disponibile"));
    renderPage(loader);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Database non disponibile",
    );
    const retry = screen.getByRole("button", { name: "Riprova" });
    retry.focus();
    await user.keyboard("{Enter}");
    expect(loader).toHaveBeenCalledTimes(2);
  });
});

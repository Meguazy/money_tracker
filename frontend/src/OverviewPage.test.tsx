import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MonthlyOverview } from "./api";
import { OverviewPage } from "./OverviewPage";

vi.mock("echarts", () => ({
  init: () => ({
    setOption: () => undefined,
    resize: () => undefined,
    dispose: () => undefined,
  }),
}));

const overview: MonthlyOverview = {
  period: { year: 2026, month: 9 },
  posted_total: { minor_units: 5000, currency: "EUR" },
  funding_split: {
    personal_funds: { minor_units: 2000, currency: "EUR" },
    benefit: { minor_units: 3000, currency: "EUR" },
    other: { minor_units: 0, currency: "EUR" },
  },
  funding_sources: [],
  budgets: [
    {
      category_name: "Spesa alimentare",
      target: { minor_units: 40000, currency: "EUR" },
      posted_spend: { minor_units: 5000, currency: "EUR" },
      remaining: { minor_units: 35000, currency: "EUR" },
    },
  ],
  unbudgeted_categories: [],
};

afterEach(() => {
  vi.restoreAllMocks();
});

function renderPage(loader: () => Promise<MonthlyOverview>) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <OverviewPage loader={loader} />
      </QueryClientProvider>
    </MantineProvider>,
  );
}

describe("OverviewPage", () => {
  it("shows exact funding and budget values with a table alternative", async () => {
    renderPage(async () => overview);

    expect(await screen.findAllByText(/50,00\s€/)).toHaveLength(3);
    expect(screen.getByText(/20,00\s€/)).toBeInTheDocument();
    expect(screen.getByText(/30,00\s€/)).toBeInTheDocument();
    expect(
      screen.getByRole("img", { name: /grafico dei budget/i }),
    ).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("table")).toHaveAccessibleName(/alternativa tabellare/i);
    expect(
      screen.getByRole("rowheader", { name: "Spesa alimentare" }),
    ).toBeInTheDocument();
  });

  it("states empty budget data instead of inventing a limit", async () => {
    renderPage(async () => ({ ...overview, budgets: [] }));

    expect(
      await screen.findByText("Non ci sono budget configurati."),
    ).toBeInTheDocument();
  });

  it("exposes an actionable error state to keyboard users", async () => {
    const user = userEvent.setup();
    const loader = vi.fn().mockRejectedValue(new Error("Database non disponibile"));
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

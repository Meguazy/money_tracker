import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { BudgetInput, BudgetMutation, BudgetPlan } from "./api";
import { BudgetsPage } from "./BudgetsPage";

const plan: BudgetPlan = {
  period: { year: 2026, month: 9 },
  entries: [
    {
      category_id: "00000000-0000-0000-0000-000000000101",
      category_name: "Spesa alimentare",
      recurring_target: { minor_units: 40000, currency: "EUR" },
      overrides: [
        {
          period: { year: 2026, month: 12 },
          target: { minor_units: 60000, currency: "EUR" },
        },
      ],
      target: { minor_units: 40000, currency: "EUR" },
      posted_spend: { minor_units: 45000, currency: "EUR" },
      remaining: { minor_units: -5000, currency: "EUR" },
      version: 3,
    },
    {
      category_id: "00000000-0000-0000-0000-000000000102",
      category_name: "Abbonamenti",
      recurring_target: { minor_units: 5000, currency: "EUR" },
      overrides: [],
      target: { minor_units: 5000, currency: "EUR" },
      posted_spend: { minor_units: 1500, currency: "EUR" },
      remaining: { minor_units: 3500, currency: "EUR" },
      version: 1,
    },
  ],
  budgetable_categories: [
    {
      id: "00000000-0000-0000-0000-000000000103",
      name: "Cura personale",
      parent_id: null,
      status: "active",
    },
  ],
};

const mutation: BudgetMutation = {
  category_id: plan.entries[0].category_id,
  version: 4,
};

function renderPage(loadResult: Promise<BudgetPlan>) {
  const apis = {
    load: vi.fn(() => loadResult),
    create: vi.fn(async (_categoryId: string, _input: BudgetInput) => mutation),
    replace: vi.fn(
      async (
        _categoryId: string,
        _input: BudgetInput & { expected_version: number },
      ) => mutation,
    ),
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <BudgetsPage apis={apis} />
      </QueryClientProvider>
    </MantineProvider>,
  );
  return apis;
}

afterEach(() => {
  cleanup();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("BudgetsPage", () => {
  it("shows each exact position with the worst one first", async () => {
    renderPage(Promise.resolve(plan));

    expect(await screen.findByText("Spesa alimentare")).toBeInTheDocument();
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("Spesa alimentare");
    expect(rows[0]).toHaveTextContent(/-50,00\s€/);
    expect(rows[0]).toHaveTextContent("1 eccezione/i configurate");
    expect(rows[1]).toHaveTextContent("Abbonamenti");
    expect(rows[1]).toHaveTextContent(/35,00\s€/);
  });

  it("creates a budget with exact cents for a category without one", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(plan));
    await screen.findByText("Spesa alimentare");

    await user.selectOptions(
      screen.getByRole("combobox", { name: "Categoria" }),
      "Cura personale",
    );
    await user.type(
      screen.getByRole("textbox", { name: /Budget mensile in euro/ }),
      "120,50",
    );
    await user.click(screen.getByRole("button", { name: "Salva budget" }));

    await waitFor(() => expect(apis.create).toHaveBeenCalledOnce());
    expect(apis.create.mock.calls[0][0]).toBe(
      plan.budgetable_categories[0].id,
    );
    expect(apis.create.mock.calls[0][1]).toEqual({
      recurring_target: { minor_units: 12050, currency: "EUR" },
      overrides: [],
    });
  });

  it("loads every stored override into a correction and quotes the version", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(plan));
    await screen.findByText("Spesa alimentare");

    await user.click(screen.getAllByRole("button", { name: "Correggi" })[0]);

    expect(
      screen.getByRole("textbox", { name: /Budget mensile in euro/ }),
    ).toHaveValue("400,00");
    expect(
      screen.getByRole("textbox", { name: "Budget eccezione 1 in euro" }),
    ).toHaveValue("600,00");
    expect(
      screen.queryByRole("combobox", { name: "Categoria" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Salva correzione" }));

    await waitFor(() => expect(apis.replace).toHaveBeenCalledOnce());
    expect(apis.replace.mock.calls[0][0]).toBe(plan.entries[0].category_id);
    expect(apis.replace.mock.calls[0][1]).toEqual({
      recurring_target: { minor_units: 40000, currency: "EUR" },
      overrides: [
        {
          period: { year: 2026, month: 12 },
          target: { minor_units: 60000, currency: "EUR" },
        },
      ],
      expected_version: 3,
    });
  });

  it("refuses a non-positive target and a duplicated exception month", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(plan));
    await screen.findByText("Spesa alimentare");

    await user.click(screen.getAllByRole("button", { name: "Correggi" })[1]);
    await user.clear(
      screen.getByRole("textbox", { name: /Budget mensile in euro/ }),
    );
    await user.type(
      screen.getByRole("textbox", { name: /Budget mensile in euro/ }),
      "-10,00",
    );
    await user.click(screen.getByRole("button", { name: "Aggiungi eccezione" }));
    await user.click(screen.getByRole("button", { name: "Aggiungi eccezione" }));
    const months = screen.getAllByLabelText(/Mese eccezione/);
    await user.type(months[0], "2026-11");
    await user.type(months[1], "2026-11");
    await user.type(
      screen.getByRole("textbox", { name: "Budget eccezione 1 in euro" }),
      "50,00",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Budget eccezione 2 in euro" }),
      "70,00",
    );
    await user.click(screen.getByRole("button", { name: "Salva correzione" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "Il budget mensile deve essere un importo positivo.",
    );
    expect(alert).toHaveTextContent("Ogni mese può avere una sola eccezione.");
    expect(apis.replace).not.toHaveBeenCalled();
  });

  it("removes an exception with the keyboard before saving", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(plan));
    await screen.findByText("Spesa alimentare");

    await user.click(screen.getAllByRole("button", { name: "Correggi" })[0]);
    const remove = screen.getByRole("button", { name: "Rimuovi eccezione 1" });
    remove.focus();
    await user.keyboard("{Enter}");

    expect(
      screen.queryByRole("textbox", { name: "Budget eccezione 1 in euro" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Salva correzione" }));

    await waitFor(() => expect(apis.replace).toHaveBeenCalledOnce());
    expect(apis.replace.mock.calls[0][1].overrides).toEqual([]);
  });

  it("keeps the form usable when the API refuses the budget", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(plan));
    apis.replace.mockRejectedValueOnce(
      new Error("Ricarica i dati prima di riprovare."),
    );
    await screen.findByText("Spesa alimentare");
    await user.click(screen.getAllByRole("button", { name: "Correggi" })[1]);
    await user.click(screen.getByRole("button", { name: "Salva correzione" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Ricarica i dati prima di riprovare.",
    );
    expect(
      screen.getByRole("textbox", { name: /Budget mensile in euro/ }),
    ).toHaveValue("50,00");
  });

  it("offers a keyboard-reachable retry when the plan fails to load", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.reject(new Error("Database non disponibile")));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Database non disponibile",
    );
    const retry = screen.getByRole("button", { name: "Riprova" });
    retry.focus();
    await user.keyboard("{Enter}");
    expect(apis.load).toHaveBeenCalledTimes(2);
  });

  it("explains the empty state instead of showing a bare table", async () => {
    renderPage(
      Promise.resolve({ ...plan, entries: [] }),
    );

    expect(
      await screen.findByText("Nessun budget configurato."),
    ).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });
});

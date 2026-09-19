import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  ExpenseInput,
  ExpenseMutation,
  TransactionRegister,
} from "./api";
import { TransactionsPage } from "./TransactionsPage";

const movement: TransactionRegister["transactions"][number] = {
  id: "00000000-0000-0000-0000-000000000301",
  amount: { minor_units: 1850, currency: "EUR" },
  occurred_at: "2026-09-12T10:30:00+02:00",
  description: "Pranzo",
  merchant: "Forno",
  status: "draft",
  category_allocations: [
    {
      category_id: "00000000-0000-0000-0000-000000000101",
      category_name: "Spesa alimentare",
      amount: { minor_units: 1850, currency: "EUR" },
      provenance: "owner:web:v1",
      evidence: null,
      owner_override: true,
      receipt_line_id: null,
    },
  ],
  payment_allocations: [
    {
      funding_source_id: "00000000-0000-0000-0000-000000000201",
      funding_source_name: "Carta",
      amount: { minor_units: 1850, currency: "EUR" },
    },
  ],
  receipt_id: null,
  version: 1,
};

const register: TransactionRegister = {
  period: { year: 2026, month: 9 },
  transactions: [movement],
  categories: [
    {
      id: "00000000-0000-0000-0000-000000000101",
      name: "Spesa alimentare",
      parent_id: null,
      status: "active",
    },
    {
      id: "00000000-0000-0000-0000-000000000102",
      name: "Prodotti casa",
      parent_id: null,
      status: "active",
    },
    {
      id: "00000000-0000-0000-0000-000000000103",
      name: "Vecchia categoria",
      parent_id: null,
      status: "archived",
    },
  ],
  funding_sources: [
    {
      id: "00000000-0000-0000-0000-000000000201",
      name: "Carta",
      funding_class: "personal_funds",
      balance_mode: "untracked",
      status: "active",
      is_default: true,
    },
    {
      id: "00000000-0000-0000-0000-000000000202",
      name: "Buoni pasto",
      funding_class: "benefit",
      balance_mode: "untracked",
      status: "active",
      is_default: false,
    },
    {
      id: "00000000-0000-0000-0000-000000000203",
      name: "Vecchia carta",
      funding_class: "personal_funds",
      balance_mode: "untracked",
      status: "archived",
      is_default: false,
    },
  ],
};

const mutation: ExpenseMutation = {
  id: movement.id,
  status: "draft",
  version: 1,
};

function renderPage(loadResult: Promise<TransactionRegister>) {
  const apis = {
    load: vi.fn(() => loadResult),
    create: vi.fn(async (_input: ExpenseInput) => mutation),
    replace: vi.fn(
      async (
        _expenseId: string,
        _input: ExpenseInput & { expected_version: number },
      ) => mutation,
    ),
    post: vi.fn(async (_expenseId: string, _version: number) => ({
      ...mutation,
      status: "posted" as const,
      version: 2,
    })),
    cancel: vi.fn(async (_expenseId: string, _version: number) => ({
      ...mutation,
      status: "discarded" as const,
      version: 2,
    })),
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <TransactionsPage apis={apis} />
      </QueryClientProvider>
    </MantineProvider>,
  );
  return apis;
}

afterEach(() => {
  cleanup();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
});

describe("TransactionsPage", () => {
  it("shows exact lifecycle facts and registers a draft", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(register));

    expect(await screen.findByText("Pranzo")).toBeInTheDocument();
    expect(screen.getByText(/18,50\s€/)).toBeInTheDocument();
    expect(screen.getAllByText("Spesa alimentare")).not.toHaveLength(0);
    expect(screen.getAllByText("Carta")).not.toHaveLength(0);
    await user.click(screen.getByRole("button", { name: "Registra" }));

    expect(apis.post).toHaveBeenCalledWith(movement.id, 1);
  });

  it("creates exact independently reconciled category and payment splits", async () => {
    // A browser outside Europe/Rome must still send the Rome wall time entered.
    vi.stubEnv("TZ", "Asia/Tokyo");
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(register));
    await screen.findByText("Pranzo");

    fireEvent.change(screen.getByLabelText(/Data e ora/), {
      target: { value: "2026-09-19T01:48" },
    });
    await user.type(
      screen.getByRole("textbox", { name: /Importo in euro/ }),
      "50,00",
    );
    await user.type(
      screen.getByRole("textbox", { name: /Descrizione/ }),
      "  Spesa mista  ",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Categoria 1" }),
      "Spesa alimentare",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Importo categoria 1 in euro" }),
      "30,00",
    );
    await user.click(screen.getByRole("button", { name: "Aggiungi categoria" }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Categoria 2" }),
      "Prodotti casa",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Importo categoria 2 in euro" }),
      "20,00",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Fonte di pagamento 1" }),
      "Buoni pasto",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Importo fonte 1 in euro" }),
      "30,00",
    );
    await user.click(screen.getByRole("button", { name: "Aggiungi fonte" }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Fonte di pagamento 2" }),
      "Carta",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Importo fonte 2 in euro" }),
      "20,00",
    );
    expect(screen.getByText(/Somma categorie: 50,00 €. Riconciliata/)).toBeVisible();
    expect(screen.getByText(/Somma fonti: 50,00 €. Riconciliata/)).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Salva come bozza" }));

    await waitFor(() => expect(apis.create).toHaveBeenCalledOnce());
    const input = apis.create.mock.calls[0][0];
    expect(input.amount).toEqual({ minor_units: 5000, currency: "EUR" });
    expect(input.occurred_at).toBe("2026-09-18T23:48:00.000Z");
    expect(input.description).toBe("Spesa mista");
    expect(input.category_allocations).toEqual([
      {
        category_id: register.categories[0].id,
        amount: { minor_units: 3000, currency: "EUR" },
        provenance: "owner:web:v1",
        evidence: null,
        owner_override: true,
        receipt_line_id: null,
      },
      {
        category_id: register.categories[1].id,
        amount: { minor_units: 2000, currency: "EUR" },
        provenance: "owner:web:v1",
        evidence: null,
        owner_override: true,
        receipt_line_id: null,
      },
    ]);
    expect(input.payment_allocations).toEqual([
      {
        funding_source_id: register.funding_sources[1].id,
        amount: { minor_units: 3000, currency: "EUR" },
      },
      {
        funding_source_id: register.funding_sources[0].id,
        amount: { minor_units: 2000, currency: "EUR" },
      },
    ]);
  });

  it("preserves every allocation and its metadata during correction", async () => {
    vi.stubEnv("TZ", "America/Los_Angeles");
    const user = userEvent.setup();
    const splitMovement: TransactionRegister["transactions"][number] = {
      ...movement,
      amount: { minor_units: 5000, currency: "EUR" },
      description: "Spesa mista",
      version: 4,
      category_allocations: [
        {
          ...movement.category_allocations[0],
          amount: { minor_units: 3000, currency: "EUR" },
          provenance: "category-fit:v6",
          evidence: "pesche",
          owner_override: false,
          receipt_line_id: "00000000-0000-0000-0000-000000000401",
        },
        {
          category_id: register.categories[1].id,
          category_name: register.categories[1].name,
          amount: { minor_units: 2000, currency: "EUR" },
          provenance: "owner:receipt:v1",
          evidence: "carta casa",
          owner_override: true,
          receipt_line_id: "00000000-0000-0000-0000-000000000402",
        },
      ],
      payment_allocations: [
        {
          funding_source_id: register.funding_sources[1].id,
          funding_source_name: register.funding_sources[1].name,
          amount: { minor_units: 3000, currency: "EUR" },
        },
        {
          funding_source_id: register.funding_sources[0].id,
          funding_source_name: register.funding_sources[0].name,
          amount: { minor_units: 2000, currency: "EUR" },
        },
      ],
    };
    const apis = renderPage(
      Promise.resolve({ ...register, transactions: [splitMovement] }),
    );
    await screen.findByText("Spesa mista");

    await user.click(screen.getByRole("button", { name: "Correggi" }));

    expect(screen.getByRole("combobox", { name: "Categoria 1" })).toHaveValue(
      register.categories[0].id,
    );
    expect(
      screen.getByRole("textbox", { name: "Importo categoria 2 in euro" }),
    ).toHaveValue("20,00");
    expect(
      screen.getByRole("combobox", { name: "Fonte di pagamento 2" }),
    ).toHaveValue(register.funding_sources[0].id);
    await user.click(screen.getByRole("button", { name: "Salva correzione" }));

    await waitFor(() => expect(apis.replace).toHaveBeenCalledOnce());
    const [expenseId, input] = apis.replace.mock.calls[0];
    expect(expenseId).toBe(splitMovement.id);
    expect(input.expected_version).toBe(4);
    // Untouched, the date keeps the recorded instant in any browser zone.
    expect(input.occurred_at).toBe("2026-09-12T08:30:00.000Z");
    expect(input.category_allocations).toEqual(
      splitMovement.category_allocations.map(
        ({ category_name: _categoryName, ...allocation }) => allocation,
      ),
    );
    expect(input.payment_allocations).toEqual(
      splitMovement.payment_allocations.map(
        ({ funding_source_name: _sourceName, ...allocation }) => allocation,
      ),
    );
  });

  it("reports category and payment reconciliation errors separately", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(register));
    await screen.findByText("Pranzo");

    await user.type(
      screen.getByRole("textbox", { name: /Importo in euro/ }),
      "50,00",
    );
    await user.type(
      screen.getByRole("textbox", { name: /Descrizione/ }),
      "Ripartizione errata",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Categoria 1" }),
      "Spesa alimentare",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Importo categoria 1 in euro" }),
      "40,00",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Fonte di pagamento 1" }),
      "Carta",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Importo fonte 1 in euro" }),
      "55,00",
    );
    await user.click(screen.getByRole("button", { name: "Salva come bozza" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(
      "La ripartizione delle categorie somma 40,00 €, mentre il totale è 50,00 €.",
    );
    expect(alert).toHaveTextContent(
      "La ripartizione delle fonti somma 55,00 €, mentre il totale è 50,00 €.",
    );
    expect(apis.create).not.toHaveBeenCalled();
  });

  it("names duplicate references, wrong signs, and invalid amounts separately", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(register));
    await screen.findByText("Pranzo");

    await user.type(
      screen.getByRole("textbox", { name: /Importo in euro/ }),
      "50,00",
    );
    await user.type(
      screen.getByRole("textbox", { name: /Descrizione/ }),
      "Ripartizione impossibile",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Categoria 1" }),
      "Spesa alimentare",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Importo categoria 1 in euro" }),
      "60,00",
    );
    await user.click(screen.getByRole("button", { name: "Aggiungi categoria" }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Categoria 2" }),
      "Spesa alimentare",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Importo categoria 2 in euro" }),
      "-10,00",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Fonte di pagamento 1" }),
      "Carta",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Importo fonte 1 in euro" }),
      "50,00,00",
    );
    await user.click(screen.getByRole("button", { name: "Salva come bozza" }));

    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Ogni categoria può comparire una sola volta.");
    expect(alert).toHaveTextContent(
      "Gli importi delle categorie devono avere lo stesso segno del totale.",
    );
    expect(alert).toHaveTextContent(
      "Inserisci un importo non nullo, con al massimo due decimali, in ogni riga delle fonti.",
    );
    expect(apis.create).not.toHaveBeenCalled();
  });

  it("marks an incomplete row invalid instead of sending it", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(register));
    await screen.findByText("Pranzo");

    await user.type(
      screen.getByRole("textbox", { name: /Importo in euro/ }),
      "12,00",
    );
    await user.type(
      screen.getByRole("textbox", { name: /Descrizione/ }),
      "Righe incomplete",
    );
    await user.click(screen.getByRole("button", { name: "Salva come bozza" }));

    expect(apis.create).not.toHaveBeenCalled();
    expect(screen.getByRole("combobox", { name: "Categoria 1" })).toBeInvalid();
    expect(
      screen.getByRole("textbox", { name: "Importo categoria 1 in euro" }),
    ).toBeInvalid();
    expect(
      screen.getByRole("combobox", { name: "Fonte di pagamento 1" }),
    ).toBeInvalid();
  });

  it("lets the owner complete a review draft that carries no category yet", async () => {
    const user = userEvent.setup();
    const unresolved: TransactionRegister["transactions"][number] = {
      ...movement,
      status: "needs_review",
      category_allocations: [],
    };
    const apis = renderPage(
      Promise.resolve({ ...register, transactions: [unresolved] }),
    );
    await screen.findByText("Pranzo");
    await user.click(screen.getByRole("button", { name: "Correggi" }));

    expect(
      screen.queryByRole("combobox", { name: "Categoria 1" }),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Salva correzione" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Aggiungi almeno una riga delle categorie.",
    );
    expect(apis.replace).not.toHaveBeenCalled();

    await user.click(screen.getByRole("button", { name: "Aggiungi categoria" }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Categoria 1" }),
      "Prodotti casa",
    );
    await user.type(
      screen.getByRole("textbox", { name: "Importo categoria 1 in euro" }),
      "18,50",
    );
    await user.click(screen.getByRole("button", { name: "Salva correzione" }));

    await waitFor(() => expect(apis.replace).toHaveBeenCalledOnce());
    expect(apis.replace.mock.calls[0][1].category_allocations).toEqual([
      {
        category_id: register.categories[1].id,
        amount: { minor_units: 1850, currency: "EUR" },
        provenance: "owner:web:v1",
        evidence: null,
        owner_override: true,
        receipt_line_id: null,
      },
    ]);
  });

  it("replaces the classification metadata only of a recategorized row", async () => {
    const user = userEvent.setup();
    const receiptMovement: TransactionRegister["transactions"][number] = {
      ...movement,
      category_allocations: [
        {
          ...movement.category_allocations[0],
          provenance: "workflow-category-fit:v6",
          evidence: "pesche",
          owner_override: false,
          receipt_line_id: "00000000-0000-0000-0000-000000000401",
        },
      ],
    };
    const apis = renderPage(
      Promise.resolve({ ...register, transactions: [receiptMovement] }),
    );
    await screen.findByText("Pranzo");
    await user.click(screen.getByRole("button", { name: "Correggi" }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Categoria 1" }),
      "Prodotti casa",
    );
    await user.click(screen.getByRole("button", { name: "Salva correzione" }));

    await waitFor(() => expect(apis.replace).toHaveBeenCalledOnce());
    expect(apis.replace.mock.calls[0][1].category_allocations).toEqual([
      {
        category_id: register.categories[1].id,
        amount: { minor_units: 1850, currency: "EUR" },
        provenance: "owner:web:v1",
        evidence: null,
        owner_override: true,
        receipt_line_id: "00000000-0000-0000-0000-000000000401",
      },
    ]);
  });

  it("shows archived references only on historical rows and supports keyboard removal", async () => {
    const user = userEvent.setup();
    const historical: TransactionRegister["transactions"][number] = {
      ...movement,
      category_allocations: [
        {
          ...movement.category_allocations[0],
          category_id: register.categories[2].id,
          category_name: register.categories[2].name,
        },
      ],
      payment_allocations: [
        {
          ...movement.payment_allocations[0],
          funding_source_id: register.funding_sources[2].id,
          funding_source_name: register.funding_sources[2].name,
        },
      ],
    };
    renderPage(Promise.resolve({ ...register, transactions: [historical] }));
    await screen.findByText("Pranzo");
    await user.click(screen.getByRole("button", { name: "Correggi" }));

    const historicalCategory = screen.getByRole("combobox", {
      name: "Categoria 1",
    });
    const historicalSource = screen.getByRole("combobox", {
      name: "Fonte di pagamento 1",
    });
    expect(
      within(historicalCategory).getByRole("option", {
        name: /Vecchia categoria \(archiviata: solo storico\)/,
      }),
    ).toBeDisabled();
    expect(
      within(historicalSource).getByRole("option", {
        name: /Vecchia carta \(archiviata: solo storico\)/,
      }),
    ).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Aggiungi categoria" }));
    const newCategory = screen.getByRole("combobox", { name: "Categoria 2" });
    expect(
      within(newCategory).queryByRole("option", { name: /Vecchia categoria/ }),
    ).not.toBeInTheDocument();
    const remove = screen.getByRole("button", { name: "Rimuovi categoria 2" });
    remove.focus();
    await user.keyboard("{Enter}");
    expect(
      screen.queryByRole("combobox", { name: "Categoria 2" }),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Aggiungi fonte" }));
    expect(
      within(
        screen.getByRole("combobox", { name: "Fonte di pagamento 2" }),
      ).queryByRole("option", { name: /Vecchia carta/ }),
    ).not.toBeInTheDocument();
  });

  it("keeps the correction visible when the API returns an error", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(register));
    apis.replace.mockRejectedValueOnce(new Error("Ricarica i dati prima di riprovare."));
    await screen.findByText("Pranzo");
    await user.click(screen.getByRole("button", { name: "Correggi" }));
    await user.click(screen.getByRole("button", { name: "Salva correzione" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Ricarica i dati prima di riprovare.",
    );
    expect(screen.getByRole("textbox", { name: "Descrizione" })).toHaveValue(
      "Pranzo",
    );
  });

  it("offers a keyboard-reachable retry when the register fails", async () => {
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
});

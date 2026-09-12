import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
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

  it("builds an exact single-allocation draft from the labelled form", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(register));
    await screen.findByText("Pranzo");

    await user.type(
      screen.getByRole("textbox", { name: /Importo in euro/ }),
      "12,34",
    );
    await user.type(
      screen.getByRole("textbox", { name: /Descrizione/ }),
      "  Colazione  ",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: /Categoria/ }),
      "Spesa alimentare",
    );
    await user.selectOptions(
      screen.getByRole("combobox", { name: /Fonte di pagamento/ }),
      "Carta",
    );
    await user.click(screen.getByRole("button", { name: "Salva come bozza" }));

    await waitFor(() => expect(apis.create).toHaveBeenCalledOnce());
    const input = apis.create.mock.calls[0][0];
    expect(input.amount).toEqual({ minor_units: 1234, currency: "EUR" });
    expect(input.description).toBe("Colazione");
    expect(input.category_allocations).toEqual([
      {
        category_id: register.categories[0].id,
        amount: { minor_units: 1234, currency: "EUR" },
        provenance: "owner:web:v1",
        owner_override: true,
      },
    ]);
    expect(input.payment_allocations[0].funding_source_id).toBe(
      register.funding_sources[0].id,
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

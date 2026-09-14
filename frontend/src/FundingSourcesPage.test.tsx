import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  FundingSourceManagement,
  FundingSourceMutation,
} from "./api";
import { FundingSourcesPage } from "./FundingSourcesPage";

const cardId = "00000000-0000-0000-0000-000000000201";
const walletId = "00000000-0000-0000-0000-000000000202";
const management: FundingSourceManagement = {
  observed_at: "2026-09-14T10:00:00Z",
  sources: [
    {
      id: cardId,
      name: "Carta principale",
      funding_class: "personal_funds",
      balance_mode: "untracked",
      status: "active",
      is_default: true,
      version: 3,
      availability: null,
      entries: [],
    },
    {
      id: walletId,
      name: "Buoni pasto",
      funding_class: "benefit",
      balance_mode: "ledger",
      status: "active",
      is_default: false,
      version: 2,
      availability: {
        booked_balance: { minor_units: 3000, currency: "EUR" },
        available_balance: { minor_units: 1800, currency: "EUR" },
        expiring: [
          {
            expires_on: "2026-09-30",
            amount: { minor_units: 1800, currency: "EUR" },
          },
        ],
      },
      entries: [
        {
          id: "00000000-0000-0000-0000-000000000301",
          entry_type: "credit",
          amount: { minor_units: 3000, currency: "EUR" },
          occurred_at: "2026-09-01T10:00:00Z",
          expires_on: "2026-09-30",
          expense_id: null,
        },
        {
          id: "00000000-0000-0000-0000-000000000302",
          entry_type: "debit",
          amount: { minor_units: -1200, currency: "EUR" },
          occurred_at: "2026-09-02T10:00:00Z",
          expires_on: null,
          expense_id: "00000000-0000-0000-0000-000000000401",
        },
      ],
    },
  ],
};

function renderPage(loadResult: Promise<FundingSourceManagement>) {
  const mutation: FundingSourceMutation = {
    id: walletId,
    name: "Buoni pasto",
    status: "active",
    is_default: true,
    version: 3,
  };
  const apis = {
    load: vi.fn((_signal?: AbortSignal) => loadResult),
    create: vi.fn(async () => mutation),
    rename: vi.fn(async () => mutation),
    archive: vi.fn(async () => ({ ...mutation, status: "archived" as const })),
    setDefault: vi.fn(async () => mutation),
    addEntry: vi.fn(async () => ({ id: crypto.randomUUID(), entry_type: "credit" as const })),
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <FundingSourcesPage apis={apis} />
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

describe("FundingSourcesPage", () => {
  it("separates payment labels from tracked wallet balances and history", async () => {
    renderPage(Promise.resolve(management));

    expect(await screen.findByText("Carta principale")).toBeInTheDocument();
    expect(screen.getByText("Fonte senza saldo tracciato")).toBeInTheDocument();
    expect(screen.getByText("Buoni pasto")).toBeInTheDocument();
    expect(screen.getByText("Disponibile ora")).toBeInTheDocument();
    expect(screen.getAllByText(/18,00\s€/)).toHaveLength(2);
    expect(screen.getAllByText(/30,00\s€/)).toHaveLength(2);
    expect(screen.getByText("Spesa")).toBeInTheDocument();
    expect(screen.getByText(/utilizzabile fino al 2026-09-30/)).toBeInTheDocument();
  });

  it("requires confirmation and sends the complete default snapshot", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(management));
    await screen.findByText("Buoni pasto");

    await user.click(screen.getByRole("button", { name: "Imposta predefinita" }));
    expect(apis.setDefault).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "Conferma come predefinita" }),
    );

    await waitFor(() => expect(apis.setDefault).toHaveBeenCalledOnce());
    expect(apis.setDefault).toHaveBeenCalledWith(
      management.sources[1],
      management.sources[0],
    );
  });

  it("prevents unsafe archive and appends an exact manual wallet credit", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(management));
    await screen.findByText("Buoni pasto");

    const archiveButtons = screen.getAllByRole("button", { name: "Archivia" });
    expect(archiveButtons).toHaveLength(2);
    expect(archiveButtons[0]).toBeDisabled();
    expect(archiveButtons[1]).toBeDisabled();

    await user.click(
      screen.getByRole("button", { name: "Registra movimento wallet" }),
    );
    await user.type(screen.getByRole("textbox", { name: "Importo in euro" }), "25,50");
    await user.type(screen.getByLabelText("Scadenza, facoltativa"), "2026-10-31");
    await user.click(screen.getByRole("button", { name: "Registra movimento" }));

    await waitFor(() => expect(apis.addEntry).toHaveBeenCalledOnce());
    expect(apis.addEntry).toHaveBeenCalledWith(
      walletId,
      expect.objectContaining({
        entry_type: "credit",
        amount: { minor_units: 2550, currency: "EUR" },
        expires_on: "2026-10-31",
      }),
    );
  });

  it("renders retryable failure and empty states", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.reject(new Error("database unavailable")));

    expect(await screen.findByRole("alert")).toHaveTextContent("Fonti non disponibili");
    await user.click(screen.getByRole("button", { name: "Riprova" }));
    expect(apis.load).toHaveBeenCalledTimes(2);

    cleanup();
    renderPage(Promise.resolve({ ...management, sources: [] }));
    expect(await screen.findByText("Nessuna fonte configurata")).toBeInTheDocument();
  });
});

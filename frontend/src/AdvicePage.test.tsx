import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AdvicePage, type AdviceRequester } from "./AdvicePage";
import type { AdviceResponse } from "./api";

function renderPage(requester: AdviceRequester) {
  const client = new QueryClient({
    defaultOptions: { mutations: { retry: false }, queries: { retry: false } },
  });
  render(
    <MantineProvider>
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <AdvicePage requester={requester} />
        </QueryClientProvider>
      </MemoryRouter>
    </MantineProvider>,
  );
}

async function ask(message = "Come vanno i budget?") {
  const user = userEvent.setup();
  await user.type(screen.getByRole("textbox", { name: "La tua domanda" }), message);
  await user.click(screen.getByRole("button", { name: "Consulta i dati" }));
  return user;
}

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("AdvicePage", () => {
  it("starts empty and renders exact category evidence without inventing a threshold", async () => {
    const requester = vi.fn<AdviceRequester>().mockResolvedValue({
      status: "answered",
      reason: null,
      evidence: {
        kind: "category_spending",
        period: { year: 2026, month: 9 },
        category_name: "Abbonamenti",
        posted_spend: { minor_units: 1799, currency: "EUR" },
        funding_split: {
          personal_funds: { minor_units: 2000, currency: "EUR" },
          benefit: { minor_units: -201, currency: "EUR" },
          other: { minor_units: 0, currency: "EUR" },
        },
        assessment_status: "spending_without_threshold",
      },
    });
    renderPage(requester);

    expect(screen.getByText("Nessuna consultazione ancora")).toBeInTheDocument();
    await ask("Quanto ho speso per gli abbonamenti?");

    expect(await screen.findByText("Spesa per Abbonamenti")).toBeInTheDocument();
    expect(screen.getByText(/17,99\s€/)).toBeInTheDocument();
    expect(screen.getByText(/-2,01\s€/)).toBeInTheDocument();
    expect(screen.getByRole("alert", { name: /Nessuna soglia/ })).toHaveTextContent(
      "non conclude se sia troppo alta",
    );
    expect(requester.mock.calls[0]?.[0]).toBe("Quanto ho speso per gli abbonamenti?");
  });

  it("renders budget positions, unbudgeted spend, and signed remaining evidence", async () => {
    const response: AdviceResponse = {
      status: "answered",
      reason: null,
      evidence: {
        kind: "budgets_at_risk",
        period: { year: 2026, month: 9 },
        positions: [
          {
            category_name: "Spesa alimentare",
            target: { minor_units: 10000, currency: "EUR" },
            posted_spend: { minor_units: 11250, currency: "EUR" },
            remaining: { minor_units: -1250, currency: "EUR" },
          },
        ],
        unbudgeted_categories: ["Abbonamenti"],
        assessment_status: "budget_evidence",
      },
    };
    renderPage(vi.fn<AdviceRequester>().mockResolvedValue(response));

    await ask();

    const row = await screen.findByRole("row", { name: /Spesa alimentare/ });
    expect(row).toHaveTextContent(/100,00\s€/);
    expect(row).toHaveTextContent(/112,50\s€/);
    expect(row).toHaveTextContent(/-12,50\s€/);
    expect(screen.getByRole("alert", { name: /Spesa senza budget/ })).toHaveTextContent(
      "Abbonamenti",
    );
  });

  it("states insufficient budget evidence explicitly", async () => {
    renderPage(
      vi.fn<AdviceRequester>().mockResolvedValue({
        status: "answered",
        reason: null,
        evidence: {
          kind: "budgets_at_risk",
          period: { year: 2026, month: 9 },
          positions: [],
          unbudgeted_categories: [],
          assessment_status: "insufficient_data",
        },
      }),
    );

    await ask();

    expect(await screen.findByRole("status", { name: /Dati insufficienti/ })).toHaveTextContent(
      "Non viene formulato alcun giudizio",
    );
    expect(screen.queryByRole("table", { name: /Obiettivi/ })).not.toBeInTheDocument();
  });

  it("distinguishes funding consumption from a wallet balance", async () => {
    renderPage(
      vi.fn<AdviceRequester>().mockResolvedValue({
        status: "answered",
        reason: null,
        evidence: {
          kind: "funding_class_split",
          period: { year: 2026, month: 9 },
          funding_split: {
            personal_funds: { minor_units: 5000, currency: "EUR" },
            benefit: { minor_units: 1800, currency: "EUR" },
            other: { minor_units: 0, currency: "EUR" },
          },
          funding_sources: [
            {
              name: "Buoni pasto",
              funding_class: "benefit",
              amount: { minor_units: 1800, currency: "EUR" },
            },
          ],
          assessment_status: "consumption_not_balance",
        },
      }),
    );

    await ask("Quanto ho usato dei buoni pasto?");

    expect(await screen.findByText("Uso delle fonti")).toBeInTheDocument();
    expect(screen.getByRole("alert", { name: /Consumo, non saldo/ })).toHaveTextContent(
      "non sono il saldo contabile o disponibile",
    );
  });

  it("renders clarification and a retryable dependency error", async () => {
    const requester = vi
      .fn<AdviceRequester>()
      .mockRejectedValueOnce(new Error("Il modello locale non è disponibile."))
      .mockResolvedValueOnce({
        status: "needs_clarification",
        evidence: null,
        reason: "request_not_understood",
      });
    renderPage(requester);
    const user = await ask("Aiutami");

    expect(await screen.findByRole("alert", { name: /Consigli non disponibili/ })).toHaveTextContent(
      "Il modello locale non è disponibile",
    );
    await user.click(screen.getByRole("button", { name: "Riprova" }));

    await waitFor(() => expect(requester).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("status", { name: /Serve una precisazione/ })).toHaveTextContent(
      "Specifica se vuoi consultare",
    );
  });
});

import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type {
  CategoryReviewQueue,
  ExpenseMutation,
} from "./api";
import type { CategoryManagement } from "./api";
import { CategoriesPage } from "./CategoriesPage";

const queue: CategoryReviewQueue = {
  proposals: [
    {
      id: "00000000-0000-0000-0000-000000000401",
      proposed_name: "Cura del giardino",
      description: "Piante, terriccio e attrezzi per il giardino.",
      parent_name: "Casa e utenze",
      rationale: "Le categorie attive non distinguono questa finalità.",
      alternative_categories: [
        {
          id: "00000000-0000-0000-0000-000000000101",
          name: "Acquisti personali",
          parent_id: null,
          status: "active",
        },
      ],
      excluded_names: ["Giardinaggio"],
      can_repropose: true,
      expense: {
        id: "00000000-0000-0000-0000-000000000301",
        amount: { minor_units: 1250, currency: "EUR" },
        occurred_at: "2026-09-12T10:30:00+02:00",
        description: "Terriccio e semi",
        merchant: "Vivaio",
        status: "draft",
        version: 1,
      },
    },
  ],
  active_categories: [
    {
      id: "00000000-0000-0000-0000-000000000101",
      name: "Acquisti personali",
      parent_id: null,
      status: "active",
    },
    {
      id: "00000000-0000-0000-0000-000000000102",
      name: "Prodotti casa, arredo e manutenzione",
      parent_id: "00000000-0000-0000-0000-000000000100",
      status: "active",
    },
  ],
};

const mutation: ExpenseMutation = {
  id: queue.proposals[0].expense.id,
  status: "posted",
  version: 3,
};

const management: CategoryManagement = {
  categories: [],
  warnings: [],
};

function renderPage(loadResult: Promise<CategoryReviewQueue>) {
  const apis = {
    load: vi.fn((_signal?: AbortSignal) => loadResult),
    approve: vi.fn(async (_proposalId: string) => mutation),
    reuse: vi.fn(async (_proposalId: string, _categoryName: string) => mutation),
    cancel: vi.fn(async (_proposalId: string) => ({
      ...mutation,
      status: "discarded" as const,
    })),
  };
  const managementApis = {
    load: vi.fn(async (_signal?: AbortSignal) => management),
    rename: vi.fn(),
    archive: vi.fn(),
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <CategoriesPage apis={apis} managementApis={managementApis} />
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

describe("CategoriesPage", () => {
  it("shows proposal, rationale, movement, and deterministic choices", async () => {
    renderPage(Promise.resolve(queue));

    expect(await screen.findByText("Cura del giardino")).toBeInTheDocument();
    expect(screen.getByText("Sotto-categoria di Casa e utenze")).toBeInTheDocument();
    expect(
      screen.getByText("Le categorie attive non distinguono questa finalità."),
    ).toBeInTheDocument();
    expect(screen.getByText("Terriccio e semi")).toBeInTheDocument();
    expect(screen.getByText(/12,50\s€/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Usa Acquisti personali" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("combobox", {
        name: "Usa un’altra categoria esistente",
      }),
    ).toBeInTheDocument();
  });

  it("approves the exact proposal from a keyboard-accessible button", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(queue));
    await screen.findByText("Cura del giardino");

    const approve = screen.getByRole("button", { name: "Approva e registra" });
    approve.focus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(apis.approve).toHaveBeenCalledOnce());
    expect(apis.approve).toHaveBeenCalledWith(queue.proposals[0].id);
  });

  it("reuses either the shortlist or a manually selected active category", async () => {
    const user = userEvent.setup();
    const shortlistApis = renderPage(Promise.resolve(queue));
    await screen.findByText("Cura del giardino");

    await user.click(
      screen.getByRole("button", { name: "Usa Acquisti personali" }),
    );

    await waitFor(() => expect(shortlistApis.reuse).toHaveBeenCalledOnce());
    expect(shortlistApis.reuse).toHaveBeenCalledWith(
      queue.proposals[0].id,
      "Acquisti personali",
    );

    cleanup();
    const manualApis = renderPage(Promise.resolve(queue));
    await screen.findByText("Cura del giardino");
    await user.selectOptions(
      screen.getByRole("combobox", {
        name: "Usa un’altra categoria esistente",
      }),
      "Prodotti casa, arredo e manutenzione",
    );
    await user.click(
      screen.getByRole("button", { name: "Usa la categoria scelta" }),
    );

    await waitFor(() => expect(manualApis.reuse).toHaveBeenCalledOnce());
    expect(manualApis.reuse).toHaveBeenCalledWith(
      queue.proposals[0].id,
      "Prodotti casa, arredo e manutenzione",
    );
  });

  it("cancels the proposal and its never-posted draft explicitly", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(queue));
    await screen.findByText("Cura del giardino");

    await user.click(
      screen.getByRole("button", { name: "Annulla proposta e bozza" }),
    );

    await waitFor(() => expect(apis.cancel).toHaveBeenCalledOnce());
    expect(apis.cancel).toHaveBeenCalledWith(queue.proposals[0].id);
  });

  it("renders empty and retryable failure states", async () => {
    const user = userEvent.setup();
    const failed = renderPage(Promise.reject(new Error("database unavailable")));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Proposte non disponibili",
    );
    await user.click(screen.getByRole("button", { name: "Riprova" }));
    expect(failed.load).toHaveBeenCalledTimes(2);

    cleanup();
    renderPage(Promise.resolve({ ...queue, proposals: [] }));
    expect(
      await screen.findByText("Nessuna proposta in attesa"),
    ).toBeInTheDocument();
  });
});

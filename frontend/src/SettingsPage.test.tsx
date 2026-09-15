import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FundingSourceMutation, ProfileSettings } from "./api";
import { SettingsPage } from "./SettingsPage";

const cardId = "00000000-0000-0000-0000-000000000201";
const benefitId = "00000000-0000-0000-0000-000000000202";
const profileSettings: ProfileSettings = {
  locale: "it-IT",
  timezone: "Europe/Rome",
  currency: "EUR",
  default_funding_source: {
    id: cardId,
    name: "Carta principale",
    funding_class: "personal_funds",
    version: 3,
  },
  available_default_sources: [
    {
      id: cardId,
      name: "Carta principale",
      funding_class: "personal_funds",
      version: 3,
    },
    {
      id: benefitId,
      name: "Buoni pasto",
      funding_class: "benefit",
      version: 2,
    },
  ],
};

function renderPage(
  loadResult: Promise<ProfileSettings>,
  setDefaultResult: () => Promise<FundingSourceMutation> = () =>
    Promise.resolve({
      id: benefitId,
      name: "Buoni pasto",
      status: "active",
      is_default: true,
      version: 3,
    }),
) {
  const apis = {
    load: vi.fn((_signal?: AbortSignal) => loadResult),
    setDefault: vi.fn(() => setDefaultResult()),
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MantineProvider>
      <MemoryRouter>
        <QueryClientProvider client={client}>
          <SettingsPage apis={apis} />
        </QueryClientProvider>
      </MemoryRouter>
    </MantineProvider>,
  );
  return apis;
}

afterEach(() => {
  cleanup();
  document.body.innerHTML = "";
  vi.restoreAllMocks();
});

describe("SettingsPage", () => {
  it("shows fixed V1 preferences as read-only facts", async () => {
    renderPage(Promise.resolve(profileSettings));

    expect(await screen.findByText("Italiano (it-IT)")).toBeInTheDocument();
    expect(screen.getByText("Euro (EUR)")).toBeInTheDocument();
    expect(screen.getByText("Europe/Rome")).toBeInTheDocument();
    expect(screen.getAllByText("Fissa in V1")).toHaveLength(3);
    expect(
      screen.getByRole("combobox", { name: "Fonte predefinita" }),
    ).toHaveValue(cardId);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("confirms and sends the complete default-source snapshot", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(profileSettings));
    const selector = await screen.findByRole("combobox", {
      name: "Fonte predefinita",
    });

    await user.selectOptions(selector, benefitId);
    await user.click(
      screen.getByRole("button", { name: "Salva fonte predefinita" }),
    );
    expect(apis.setDefault).not.toHaveBeenCalled();
    expect(screen.getByRole("alert")).toHaveTextContent(
      "I movimenti già registrati non cambieranno",
    );

    await user.click(screen.getByRole("button", { name: "Conferma modifica" }));

    await waitFor(() => expect(apis.setDefault).toHaveBeenCalledOnce());
    expect(apis.setDefault).toHaveBeenCalledWith(
      profileSettings.available_default_sources[1],
      profileSettings.default_funding_source,
    );
    expect(
      await screen.findByRole("status", { name: "Impostazione aggiornata" }),
    ).toHaveTextContent("fonte predefinita è stata aggiornata");
  });

  it("lets the owner cancel a sensitive change before it is sent", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(profileSettings));
    const selector = await screen.findByRole("combobox", {
      name: "Fonte predefinita",
    });

    await user.selectOptions(selector, benefitId);
    await user.click(
      screen.getByRole("button", { name: "Salva fonte predefinita" }),
    );
    await user.click(screen.getByRole("button", { name: "Annulla" }));

    expect(apis.setDefault).not.toHaveBeenCalled();
    expect(screen.queryByText("Conferma il cambio")).not.toBeInTheDocument();
  });

  it("renders retryable load failure and an actionable empty state", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.reject(new Error("database unavailable")));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Impostazioni non disponibili",
    );
    await user.click(screen.getByRole("button", { name: "Riprova" }));
    expect(apis.load).toHaveBeenCalledTimes(2);

    cleanup();
    renderPage(
      Promise.resolve({
        ...profileSettings,
        default_funding_source: null,
        available_default_sources: [],
      }),
    );
    expect(await screen.findByText("Nessuna fonte disponibile")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Vai alle fonti" })).toHaveAttribute(
      "href",
      "/fonti",
    );
  });

  it("shows a bounded mutation error without changing fixed preferences", async () => {
    const user = userEvent.setup();
    renderPage(
      Promise.resolve(profileSettings),
      () => Promise.reject(new Error("La configurazione è cambiata. Ricarica e riprova.")),
    );
    const selector = await screen.findByRole("combobox", {
      name: "Fonte predefinita",
    });

    await user.selectOptions(selector, benefitId);
    await user.click(
      screen.getByRole("button", { name: "Salva fonte predefinita" }),
    );
    await user.click(screen.getByRole("button", { name: "Conferma modifica" }));

    expect(await screen.findByText("Modifica non applicata")).toBeInTheDocument();
    expect(
      screen.getByText("La configurazione è cambiata. Ricarica e riprova."),
    ).toBeInTheDocument();
    expect(screen.getByText("Italiano (it-IT)")).toBeInTheDocument();
    expect(screen.getByText("Euro (EUR)")).toBeInTheDocument();
    expect(screen.getByText("Europe/Rome")).toBeInTheDocument();
  });
});

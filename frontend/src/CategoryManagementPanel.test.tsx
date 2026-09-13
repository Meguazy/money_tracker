import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CategoryManagement, CategoryMutation } from "./api";
import { CategoryManagementPanel } from "./CategoryManagementPanel";

const rootId = "00000000-0000-0000-0000-000000000101";
const childId = "00000000-0000-0000-0000-000000000102";

const management: CategoryManagement = {
  categories: [
    {
      id: rootId,
      name: "Casa",
      description: "Spese per la casa.",
      parent_id: null,
      parent_name: null,
      aliases: ["Abitazione"],
      status: "active",
      seeded: true,
      version: 2,
    },
    {
      id: childId,
      name: "Arredo",
      description: "Mobili e accessori.",
      parent_id: rootId,
      parent_name: "Casa",
      aliases: [],
      status: "active",
      seeded: true,
      version: 3,
    },
    {
      id: "00000000-0000-0000-0000-000000000103",
      name: "Storica",
      description: "Categoria mantenuta nello storico.",
      parent_id: null,
      parent_name: null,
      aliases: [],
      status: "archived",
      seeded: false,
      version: 4,
    },
  ],
  warnings: [{ code: "active_category_limit", message: "Tassonomia ampia." }],
};

function renderPanel(loadResult: Promise<CategoryManagement>) {
  const updated: CategoryMutation = {
    id: childId,
    name: "Interni",
    status: "active",
    version: 4,
  };
  const apis = {
    load: vi.fn((_signal?: AbortSignal) => loadResult),
    rename: vi.fn(async () => updated),
    archive: vi.fn(async () => ({ ...updated, status: "archived" as const })),
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <CategoryManagementPanel apis={apis} />
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

describe("CategoryManagementPanel", () => {
  it("shows hierarchy, aliases, archived history, and complexity warnings", async () => {
    renderPanel(Promise.resolve(management));

    expect(await screen.findByText("Casa")).toBeInTheDocument();
    expect(screen.getByText("Sotto-categoria di Casa")).toBeInTheDocument();
    expect(screen.getByText("Alias: Abitazione")).toBeInTheDocument();
    expect(screen.getByText("Storica")).toBeInTheDocument();
    expect(screen.getByText("Tassonomia ampia.")).toBeInTheDocument();

    const archiveButtons = screen.getAllByRole("button", { name: "Archivia" });
    expect(archiveButtons[0]).toBeDisabled();
    expect(
      screen.getByText("Archivia prima le sotto-categorie attive."),
    ).toBeInTheDocument();
  });

  it("renames from the displayed optimistic version", async () => {
    const user = userEvent.setup();
    const apis = renderPanel(Promise.resolve(management));
    await screen.findByText("Arredo");

    const renameButtons = screen.getAllByRole("button", { name: "Rinomina" });
    await user.click(renameButtons[1]);
    const input = screen.getByRole("textbox", { name: "Nuovo nome per Arredo" });
    await user.clear(input);
    await user.type(input, "Interni");
    await user.click(screen.getByRole("button", { name: "Salva nome" }));

    await waitFor(() => expect(apis.rename).toHaveBeenCalledOnce());
    expect(apis.rename).toHaveBeenCalledWith(childId, "Interni", 3);
  });

  it("requires explicit confirmation before archiving a leaf", async () => {
    const user = userEvent.setup();
    const apis = renderPanel(Promise.resolve(management));
    await screen.findByText("Arredo");

    const archiveButtons = screen.getAllByRole("button", { name: "Archivia" });
    await user.click(archiveButtons[1]);
    expect(apis.archive).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "Conferma archiviazione" }),
    );

    await waitFor(() => expect(apis.archive).toHaveBeenCalledOnce());
    expect(apis.archive).toHaveBeenCalledWith(childId, 3);
  });

  it("offers a retry when category loading fails", async () => {
    const user = userEvent.setup();
    const apis = renderPanel(Promise.reject(new Error("database unavailable")));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Categorie non disponibili",
    );
    await user.click(screen.getByRole("button", { name: "Riprova" }));
    expect(apis.load).toHaveBeenCalledTimes(2);
  });
});

import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ReceiptMutation, ReceiptRegister } from "./api";
import { ReceiptsPage, formatByteSize } from "./ReceiptsPage";

const register: ReceiptRegister = {
  receipts: [
    {
      id: "00000000-0000-0000-0000-000000000601",
      media_type: "image/jpeg",
      byte_size: 204800,
      stored_at: "2026-09-12T18:05:00+02:00",
      status: "stored",
      original_filename: "mercato.jpg",
      version: 1,
    },
    {
      id: "00000000-0000-0000-0000-000000000602",
      media_type: "image/png",
      byte_size: 4096,
      stored_at: "2026-09-11T09:30:00+02:00",
      status: "needs_review",
      original_filename: "farmacia.png",
      version: 2,
    },
    {
      id: "00000000-0000-0000-0000-000000000603",
      media_type: "image/webp",
      byte_size: 900,
      stored_at: "2026-09-10T08:00:00+02:00",
      status: "approved",
      original_filename: null,
      version: 3,
    },
  ],
};

function mutationFor(id: string, version: number): ReceiptMutation {
  return { id, status: "needs_review", version: version + 1 };
}

function renderPage(loadResult: Promise<ReceiptRegister>) {
  const apis = {
    load: vi.fn((_signal?: AbortSignal) => loadResult),
    review: vi.fn(async (receiptId: string, expectedVersion: number) =>
      mutationFor(receiptId, expectedVersion),
    ),
    approve: vi.fn(async (receiptId: string, expectedVersion: number) => ({
      ...mutationFor(receiptId, expectedVersion),
      status: "approved" as const,
    })),
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <ReceiptsPage apis={apis} />
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

describe("ReceiptsPage", () => {
  it("shows retained metadata and never a content digest or preview", async () => {
    renderPage(Promise.resolve(register));

    expect(await screen.findByText("mercato.jpg")).toBeInTheDocument();
    expect(screen.getByText("JPEG")).toBeInTheDocument();
    expect(screen.getByText("200,0 KiB")).toBeInTheDocument();
    expect(screen.getByText("Archiviato")).toBeInTheDocument();
    expect(screen.getByText("Da approvare")).toBeInTheDocument();
    expect(screen.getByText("Senza nome")).toBeInTheDocument();
    expect(screen.queryAllByRole("img")).toHaveLength(0);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });

  it("opens the review of a stored receipt under its displayed version", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(register));
    await screen.findByText("mercato.jpg");

    const open = screen.getByRole("button", {
      name: "Apri la revisione di mercato.jpg",
    });
    open.focus();
    expect(open).toHaveFocus();
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(apis.review).toHaveBeenCalledWith(
        "00000000-0000-0000-0000-000000000601",
        1,
      );
    });
    expect(apis.approve).not.toHaveBeenCalled();
  });

  it("approves only a receipt that already entered mandatory review", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(register));
    await screen.findByText("farmacia.png");

    expect(
      screen.queryByRole("button", { name: "Approva mercato.jpg" }),
    ).not.toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Approva farmacia.png" }),
    );

    await waitFor(() => {
      expect(apis.approve).toHaveBeenCalledWith(
        "00000000-0000-0000-0000-000000000602",
        2,
      );
    });
    expect(screen.getByText("Revisione completata.")).toBeInTheDocument();
  });

  it("reports a refused decision without losing the listed receipts", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(register));
    apis.approve.mockRejectedValueOnce(
      new Error("Ricarica gli scontrini prima di riprovare."),
    );
    await screen.findByText("farmacia.png");

    await user.click(
      screen.getByRole("button", { name: "Approva farmacia.png" }),
    );

    expect(
      await screen.findByText("Ricarica gli scontrini prima di riprovare."),
    ).toBeInTheDocument();
    expect(screen.getByText("farmacia.png")).toBeInTheDocument();
  });

  it("offers a retry when the archive cannot be read", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.reject(new Error("non disponibile")));

    await user.click(await screen.findByRole("button", { name: "Riprova" }));

    await waitFor(() => {
      expect(apis.load).toHaveBeenCalledTimes(2);
    });
  });

  it("states plainly that nothing has been archived yet", async () => {
    renderPage(Promise.resolve({ receipts: [] }));

    expect(
      await screen.findByText("Nessuno scontrino conservato"),
    ).toBeInTheDocument();
  });
});

describe("formatByteSize", () => {
  it("uses exact units with an Italian decimal separator", () => {
    expect(formatByteSize(900)).toBe("900 B");
    expect(formatByteSize(4096)).toBe("4,0 KiB");
    expect(formatByteSize(20 * 1024 * 1024)).toBe("20,0 MiB");
  });
});

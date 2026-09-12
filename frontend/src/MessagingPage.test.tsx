import { MantineProvider } from "@mantine/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { MessagingOperations, MessagingRequeue } from "./api";
import { MessagingPage, describeError } from "./MessagingPage";

const operations: MessagingOperations = {
  environment: "test",
  owner_paired: true,
  next_offset: 412,
  last_poll_at: "2026-09-12T18:00:00+02:00",
  last_observed_at: "2026-09-12T17:55:00+02:00",
  downtime_exceeds_update_retention: false,
  active_actions: 3,
  inbox: {
    pending: 2,
    retrying: 1,
    dead_letters: 1,
    oldest_at: "2026-09-12T17:40:00+02:00",
  },
  outbox: {
    pending: 0,
    retrying: 0,
    dead_letters: 1,
    oldest_at: null,
  },
  inbox_dead_letters: [
    {
      inbox_id: "00000000-0000-0000-0000-0000000007d1",
      update_id: 907,
      attempt_count: 5,
      replay_count: 1,
      last_error_code: "model_unavailable",
      accepted_at: "2026-09-12T17:40:00+02:00",
    },
  ],
  outbox_dead_letters: [
    {
      outbox_id: "00000000-0000-0000-0000-0000000007d2",
      response_kind: "draft",
      attempt_count: 5,
      replay_count: 0,
      last_error_code: "telegram_unavailable",
      created_at: "2026-09-12T17:45:00+02:00",
    },
  ],
};

function renderPage(loadResult: Promise<MessagingOperations>) {
  const apis = {
    load: vi.fn((_signal?: AbortSignal) => loadResult),
    replay: vi.fn(async (inboxId: string): Promise<MessagingRequeue> => ({
      id: inboxId,
      queue: "inbox",
      outcome: "applied",
    })),
    resend: vi.fn(async (outboxId: string): Promise<MessagingRequeue> => ({
      id: outboxId,
      queue: "outbox",
      outcome: "applied",
    })),
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <MantineProvider>
      <QueryClientProvider client={client}>
        <MessagingPage apis={apis} />
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

describe("MessagingPage", () => {
  it("shows queue state and failure reasons without any message text", async () => {
    renderPage(Promise.resolve(operations));

    expect(await screen.findByText("Ambiente Test")).toBeInTheDocument();
    expect(screen.getByText("412")).toBeInTheDocument();
    expect(screen.getByText("#907")).toBeInTheDocument();
    expect(screen.getByText("Modello non raggiungibile")).toBeInTheDocument();
    expect(screen.getByText("Telegram non raggiungibile")).toBeInTheDocument();
    expect(screen.getByText("draft")).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("bot_key");
    expect(document.body.textContent).not.toContain("source_reference");
  });

  it("replays a failed work item under the count it displayed", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(operations));
    await screen.findByText("#907");

    const replay = screen.getByRole("button", {
      name: "Rimetti in coda l’aggiornamento 907",
    });
    replay.focus();
    expect(replay).toHaveFocus();
    await user.keyboard("{Enter}");

    await waitFor(() => {
      expect(apis.replay).toHaveBeenCalledWith(
        "00000000-0000-0000-0000-0000000007d1",
        1,
      );
    });
    expect(apis.resend).not.toHaveBeenCalled();
  });

  it("resends an undelivered response and warns about the duplicate risk", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(operations));
    await screen.findByText("draft");

    expect(
      screen.getByText(/non offre una chiave di idempotenza/),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "Rinvia la risposta draft" }),
    );

    await waitFor(() => {
      expect(apis.resend).toHaveBeenCalledWith(
        "00000000-0000-0000-0000-0000000007d2",
        0,
      );
    });
    expect(apis.replay).not.toHaveBeenCalled();
  });

  it("reports a refused requeue without losing the listed failures", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.resolve(operations));
    apis.replay.mockRejectedValueOnce(
      new Error("Ricarica lo stato della messaggistica prima di riprovare."),
    );
    await screen.findByText("#907");

    await user.click(
      screen.getByRole("button", {
        name: "Rimetti in coda l’aggiornamento 907",
      }),
    );

    expect(
      await screen.findByText(
        "Ricarica lo stato della messaggistica prima di riprovare.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("#907")).toBeInTheDocument();
  });

  it("warns when the bot has no paired owner and when updates expired", async () => {
    renderPage(
      Promise.resolve({
        ...operations,
        owner_paired: false,
        downtime_exceeds_update_retention: true,
      }),
    );

    expect(
      await screen.findByText("Nessun proprietario associato"),
    ).toBeInTheDocument();
    expect(screen.getByText("Interruzione prolungata")).toBeInTheDocument();
  });

  it("states plainly that both queues are healthy", async () => {
    renderPage(
      Promise.resolve({
        ...operations,
        inbox: { ...operations.inbox, dead_letters: 0 },
        outbox: { ...operations.outbox, dead_letters: 0 },
        inbox_dead_letters: [],
        outbox_dead_letters: [],
      }),
    );

    expect(
      await screen.findByText(
        "Nessun lavoro in ingresso ha esaurito i tentativi.",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Nessuna risposta in uscita ha esaurito i tentativi."),
    ).toBeInTheDocument();
  });

  it("offers a retry when the messaging state cannot be read", async () => {
    const user = userEvent.setup();
    const apis = renderPage(Promise.reject(new Error("non disponibile")));

    await user.click(await screen.findByRole("button", { name: "Riprova" }));

    await waitFor(() => {
      expect(apis.load).toHaveBeenCalledTimes(2);
    });
  });
});

describe("describeError", () => {
  it("names known reason codes and passes an unknown one through", () => {
    expect(describeError("telegram_rejected")).toBe(
      "Telegram ha rifiutato l’invio",
    );
    expect(describeError("some_new_code")).toBe("some_new_code");
    expect(describeError(null)).toBe("Nessun codice registrato");
  });
});

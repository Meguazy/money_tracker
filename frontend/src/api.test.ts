import { afterEach, describe, expect, it, vi } from "vitest";

import { requestAdvice } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("requestAdvice", () => {
  it("uses CSRF without assigning mutation authority or putting text in the URL", async () => {
    const fetcher = vi
      .fn<typeof fetch>()
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ token: "csrf-value" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            status: "needs_clarification",
            evidence: null,
            reason: "request_not_understood",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
      );
    vi.stubGlobal("fetch", fetcher);

    await requestAdvice("Come vanno i budget?");

    expect(fetcher).toHaveBeenNthCalledWith(1, "/api/v1/csrf");
    expect(fetcher).toHaveBeenNthCalledWith(
      2,
      "/api/v1/advice",
      expect.objectContaining({
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": "csrf-value",
        },
        body: JSON.stringify({ message: "Come vanno i budget?" }),
      }),
    );
    const options = fetcher.mock.calls[1]?.[1] as RequestInit;
    expect(options.headers).not.toHaveProperty("Idempotency-Key");
  });
});

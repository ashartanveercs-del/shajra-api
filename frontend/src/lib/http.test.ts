import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiProblem, requestJson } from "./http";

describe("requestJson", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns parsed JSON from a successful response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ id: "member-1" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(requestJson<{ id: string }>("/api/members/member-1")).resolves.toEqual({
      id: "member-1",
    });
  });

  it("turns structured API errors into ApiProblem values", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            detail: {
              code: "PUBLIC_WRITES_DISABLED",
              message: "Public writes are disabled.",
            },
          }),
          {
            status: 503,
            headers: { "Content-Type": "application/json", "x-request-id": "req-123" },
          },
        ),
      ),
    );

    await expect(requestJson("/api/submit", { method: "POST" })).rejects.toMatchObject({
      status: 503,
      code: "PUBLIC_WRITES_DISABLED",
      message: "Public writes are disabled.",
      requestId: "req-123",
    });
  });

  it("uses legacy string detail as the error message", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ detail: "Member not found" }), {
          status: 404,
          headers: { "Content-Type": "application/json" },
        }),
      ),
    );

    await expect(requestJson("/api/members/missing")).rejects.toMatchObject({
      status: 404,
      code: "REQUEST_FAILED",
      message: "Member not found",
    });
  });

  it("preserves a useful typed problem for non-JSON failures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("Service unavailable", { status: 503 })),
    );

    await expect(requestJson("/api/tree")).rejects.toMatchObject({
      status: 503,
      code: "REQUEST_FAILED",
      message: "Request failed with 503",
    });
  });

  it("returns undefined for an empty successful response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));

    await expect(requestJson<void>("/api/admin/complete", { method: "POST" })).resolves.toBeUndefined();
  });

  it("rejects a non-empty successful response that is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockImplementation(() => Promise.resolve(new Response("ok", { status: 200 }))),
    );

    await expect(requestJson("/api/health/live")).rejects.toBeInstanceOf(ApiProblem);
    await expect(requestJson("/api/health/live")).rejects.toMatchObject({
      status: 200,
      code: "INVALID_RESPONSE",
      message: "Expected a JSON response",
    });
  });
});

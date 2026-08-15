import { afterEach, describe, expect, it, vi } from "vitest";

const TEST_API_BASE = "http://api.test";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

async function loadApi() {
  vi.stubEnv("NODE_ENV", "test");
  vi.stubEnv("NEXT_PUBLIC_API_URL", `${TEST_API_BASE}///`);
  vi.resetModules();
  return import("./api");
}

describe("frontend API contracts", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("routes a public tree read through the HTTP boundary", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse([{ id: "member-1" }]));
    vi.stubGlobal("fetch", fetchSpy);
    const { fetchTree } = await loadApi();

    await expect(fetchTree()).resolves.toEqual([{ id: "member-1" }]);
    expect(fetchSpy).toHaveBeenCalledWith(`${TEST_API_BASE}/api/tree`, {
      cache: "no-store",
    });
  });

  it("routes an admin read with its bearer token through the HTTP boundary", async () => {
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse([{ id: "pending-1" }]));
    vi.stubGlobal("fetch", fetchSpy);
    const { fetchPending } = await loadApi();

    await expect(fetchPending("admin-token")).resolves.toEqual([{ id: "pending-1" }]);
    expect(fetchSpy).toHaveBeenCalledWith(`${TEST_API_BASE}/api/admin/pending`, {
      headers: {
        Authorization: "Bearer admin-token",
        "Content-Type": "application/json",
      },
    });
  });

  it("maps adminFetchIntegrations to read-only integration status", async () => {
    const integrations = {
      groqConfigured: true,
      cloudinaryConfigured: false,
      coordinationConfigured: false,
    };
    const fetchSpy = vi.fn().mockResolvedValue(jsonResponse(integrations));
    vi.stubGlobal("fetch", fetchSpy);
    const { adminFetchIntegrations } = await loadApi();

    await expect(adminFetchIntegrations("admin-token")).resolves.toEqual(integrations);
    expect(fetchSpy).toHaveBeenCalledWith(`${TEST_API_BASE}/api/admin/integrations`, {
      headers: {
        Authorization: "Bearer admin-token",
        "Content-Type": "application/json",
      },
    });
  });

  it("does not export removed healing or credential-write capabilities", async () => {
    const fetchSpy = vi.fn();
    vi.stubGlobal("fetch", fetchSpy);
    const api = await loadApi();

    expect(api).not.toHaveProperty(["admin", "Update", "Settings"].join(""));
    expect(api).not.toHaveProperty(["admin", "Heal"].join(""));
    expect(api).not.toHaveProperty(["admin", "Fetch", "Settings"].join(""));
    expect(fetchSpy).not.toHaveBeenCalled();
  });

  it("propagates typed read failures instead of manufacturing empty results", async () => {
    const fetchSpy = vi.fn().mockImplementation(() =>
      Promise.resolve(
        jsonResponse(
          {
            detail: {
              code: "SERVICE_UNAVAILABLE",
              message: "Service unavailable.",
            },
          },
          503,
        ),
      ),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const api = await loadApi();

    const expectedProblem = {
      status: 503,
      code: "SERVICE_UNAVAILABLE",
      message: "Service unavailable.",
    };

    await expect(api.searchMembers("Ali")).rejects.toMatchObject(expectedProblem);
    await expect(api.fetchComments("member-1")).rejects.toMatchObject(expectedProblem);
    await expect(api.fetchStories()).rejects.toMatchObject(expectedProblem);
    await expect(api.fetchAlbums()).rejects.toMatchObject(expectedProblem);
    await expect(api.verifyEmail("family@example.com")).rejects.toMatchObject(
      expectedProblem,
    );
    await expect(api.adminGetHistory("admin-token")).rejects.toMatchObject(
      expectedProblem,
    );
    expect(fetchSpy).toHaveBeenCalledTimes(6);
  });

  it("preserves legitimate empty reads and an unapproved email response", async () => {
    const fetchSpy = vi.fn().mockImplementation((input: string) =>
      Promise.resolve(
        jsonResponse(input.includes("/api/verify-email") ? { approved: false } : []),
      ),
    );
    vi.stubGlobal("fetch", fetchSpy);
    const api = await loadApi();

    await expect(api.searchMembers("Nobody")).resolves.toEqual([]);
    await expect(api.fetchComments("member-1")).resolves.toEqual([]);
    await expect(api.fetchStories()).resolves.toEqual([]);
    await expect(api.fetchAlbums()).resolves.toEqual([]);
    await expect(api.adminGetHistory("admin-token")).resolves.toEqual([]);
    await expect(api.verifyEmail("family@example.com")).resolves.toBe(false);
    expect(fetchSpy).toHaveBeenCalledTimes(6);
  });

});

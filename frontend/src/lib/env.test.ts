import { describe, expect, it } from "vitest";

import { resolveApiBase } from "./env";

describe("resolveApiBase", () => {
  it("removes trailing slashes from a configured API URL", () => {
    expect(
      resolveApiBase({
        NODE_ENV: "production",
        NEXT_PUBLIC_API_URL: "https://api.example.com///",
      }),
    ).toBe("https://api.example.com");
  });

  it("rejects production without a configured API URL", () => {
    expect(() => resolveApiBase({ NODE_ENV: "production" })).toThrow(
      "NEXT_PUBLIC_API_URL",
    );
  });

  it("uses localhost only during development and test", () => {
    expect(resolveApiBase({ NODE_ENV: "test" })).toBe("http://127.0.0.1:8000");
  });
});

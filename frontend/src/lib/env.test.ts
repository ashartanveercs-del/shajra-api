import { readFileSync } from "node:fs";
import path from "node:path";

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

  it("initializes API_BASE from references Next.js can inline", () => {
    const source = readFileSync(path.resolve(process.cwd(), "src/lib/env.ts"), "utf8");

    expect(source).toContain("process.env.NODE_ENV");
    expect(source).toContain("process.env.NEXT_PUBLIC_API_URL");
  });
});

import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

type SourceFile = {
  path: string;
  source: string;
};

const SOURCE_EXTENSIONS = new Set([".cjs", ".css", ".js", ".jsx", ".mjs", ".ts", ".tsx"]);
const DIRECT_FETCH = /\bfetch\s*\(/;
const NEXT_ROUTE_HANDLER = /^app\/(?:.*\/)?route\.(?:cjs|js|jsx|mjs|ts|tsx)$/;
const RAILWAY_PATTERNS = [
  new RegExp(["railway", "\\.app"].join(""), "i"),
  new RegExp(["shajra-api", "production"].join("-"), "i"),
];

function readSourceTree(root: string, directory = root): SourceFile[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const absolutePath = path.join(directory, entry.name);
    if (entry.isDirectory()) return readSourceTree(root, absolutePath);
    if (!entry.isFile() || !SOURCE_EXTENSIONS.has(path.extname(entry.name))) return [];
    return [
      {
        path: path.relative(root, absolutePath).split(path.sep).join("/"),
        source: readFileSync(absolutePath, "utf8"),
      },
    ];
  });
}

function findSourceViolations(files: SourceFile[]): string[] {
  return files.flatMap((file) => {
    const violations: string[] = [];
    const allowsDirectFetch =
      file.path === "lib/http.ts" || NEXT_ROUTE_HANDLER.test(file.path);

    if (DIRECT_FETCH.test(file.source) && !allowsDirectFetch) {
      violations.push(`${file.path}: direct network call outside HTTP boundary`);
    }
    if (RAILWAY_PATTERNS.some((pattern) => pattern.test(file.source))) {
      violations.push(`${file.path}: forbidden Railway reference`);
    }
    return violations;
  });
}

describe("frontend source invariants", () => {
  it("rejects direct network calls outside the HTTP boundary", () => {
    const unauthorizedCall = ["return ", "fetch", "('/api/tree');"].join("");

    expect(findSourceViolations([{ path: "lib/api.ts", source: unauthorizedCall }])).toEqual([
      "lib/api.ts: direct network call outside HTTP boundary",
    ]);
  });

  it("permits direct network calls in Next route handlers", () => {
    const routeCall = ["return ", "fetch", "('http://backend.test');"].join("");

    expect(
      findSourceViolations([{ path: "app/api/members/route.ts", source: routeCall }]),
    ).toEqual([]);
  });

  it("rejects Railway references", () => {
    const forbiddenHost = ["https://service.up", ["railway", "app"].join(".")].join(".");

    expect(findSourceViolations([{ path: "lib/env.ts", source: forbiddenHost }])).toEqual([
      "lib/env.ts: forbidden Railway reference",
    ]);
  });

  it("keeps the recursively scanned frontend source within the boundary", () => {
    const sourceRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

    expect(findSourceViolations(readSourceTree(sourceRoot))).toEqual([]);
  });
});

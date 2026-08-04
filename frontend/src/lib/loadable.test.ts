import { describe, expect, it } from "vitest";

import { ApiProblem } from "./http";
import { asApiProblem } from "./loadable";

describe("asApiProblem", () => {
  it("uses canonical public copy for a documented write gate", () => {
    const problem = new ApiProblem(
      503,
      "PUBLIC_WRITES_DISABLED",
      "untrusted replacement text",
      "request-1",
    );

    expect(asApiProblem(problem, "The submission could not be sent.")).toMatchObject({
      status: 503,
      code: "PUBLIC_WRITES_DISABLED",
      message: "Submissions are temporarily unavailable.",
      requestId: "request-1",
    });
  });

  it("redacts raw typed backend detail while retaining problem metadata", () => {
    const problem = asApiProblem(
      new ApiProblem(
        500,
        "REQUEST_FAILED",
        "Airtable rejected token secret-token in response body",
        "request-2",
      ),
      "Family records could not be loaded.",
    );

    expect(problem).toMatchObject({
      status: 500,
      code: "REQUEST_FAILED",
      message: "Family records could not be loaded.",
      requestId: "request-2",
    });
    expect(problem.message).not.toContain("secret-token");
  });

  it("hides arbitrary exception details behind safe route copy", () => {
    const problem = asApiProblem(
      new Error("socket failed with secret-token"),
      "Could not load family records.",
    );

    expect(problem).toMatchObject({
      status: 0,
      code: "CLIENT_REQUEST_FAILED",
      message: "Could not load family records.",
    });
    expect(problem.message).not.toContain("secret-token");
  });
});

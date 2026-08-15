import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "@/lib/http";

const apiMocks = vi.hoisted(() => ({ fetchMembers: vi.fn() }));

vi.mock("@/lib/api", () => apiMocks);

import HomePage from "./page";

describe("HomePage member states", () => {
  beforeEach(() => apiMocks.fetchMembers.mockReset());

  it("keeps navigation visible and retries a failed archive read into empty", async () => {
    apiMocks.fetchMembers
      .mockRejectedValueOnce(
        new ApiProblem(500, "REQUEST_FAILED", "raw provider detail secret-token"),
      )
      .mockResolvedValueOnce([]);
    const user = userEvent.setup();

    render(<HomePage />);

    expect(screen.getByRole("link", { name: /Explore the Tree/i })).toBeInTheDocument();
    expect(await screen.findByRole("alert")).toHaveTextContent("Family archive unavailable");
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret-token");
    expect(screen.queryByText("0")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("No family members yet")).toBeInTheDocument();
    expect(apiMocks.fetchMembers).toHaveBeenCalledTimes(2);
  });
});

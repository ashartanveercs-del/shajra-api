import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "@/lib/http";

const apiMocks = vi.hoisted(() => ({ fetchMembers: vi.fn() }));

vi.mock("@/lib/api", () => apiMocks);

import SearchPage from "./page";

describe("SearchPage load states", () => {
  beforeEach(() => {
    apiMocks.fetchMembers.mockReset();
  });

  it("gives each directory filter a persistent accessible name", async () => {
    apiMocks.fetchMembers.mockResolvedValue([
      {
        id: "member-1",
        FullName: "Ali Khan",
        CurrentCity: "Lahore",
        Branch: "Khan",
        Generation: 3,
      },
    ]);

    render(<SearchPage />);

    expect(await screen.findByRole("combobox", { name: "City" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Branch" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Generation" })).toBeInTheDocument();
  });

  it("preserves the query while retrying a failed directory read", async () => {
    apiMocks.fetchMembers
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "raw failure"))
      .mockResolvedValueOnce([{ id: "member-1", FullName: "Ali Khan" }]);
    const user = userEvent.setup();

    render(<SearchPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Directory unavailable");
    const search = screen.getByRole("textbox", { name: "Search by name" });
    await user.type(search, "Ali");
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Ali Khan")).toBeInTheDocument();
    expect(search).toHaveValue("Ali");
    expect(apiMocks.fetchMembers).toHaveBeenCalledTimes(2);
  });
});

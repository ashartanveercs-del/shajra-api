import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "@/lib/http";

const apiMocks = vi.hoisted(() => ({ searchMembers: vi.fn() }));

vi.mock("@/lib/api", () => ({ searchMembers: apiMocks.searchMembers }));

import SearchPage from "./page";

const ALI = { id: "member-1", FullName: "Ali Khan", CurrentCity: "Lahore", Branch: "Khan", Generation: 3 };

describe("SearchPage", () => {
  beforeEach(() => {
    apiMocks.searchMembers.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("gives each directory filter a persistent accessible name", async () => {
    apiMocks.searchMembers.mockResolvedValue([ALI]);

    render(<SearchPage />);

    expect(await screen.findByRole("combobox", { name: "City" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Branch" })).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Generation" })).toBeInTheDocument();
  });

  it("searches through the search endpoint with a debounced query", async () => {
    vi.useFakeTimers();
    apiMocks.searchMembers.mockResolvedValue([]);
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    render(<SearchPage />);

    expect(await screen.findByText("No family members yet")).toBeInTheDocument();
    expect(apiMocks.searchMembers).toHaveBeenCalledTimes(1);
    expect(apiMocks.searchMembers).toHaveBeenLastCalledWith("", {});

    const search = screen.getByRole("textbox", { name: "Search by name" });
    await user.type(search, "Ali");

    // Debounce: nothing fires until the pause elapses.
    expect(apiMocks.searchMembers).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(300);

    expect(apiMocks.searchMembers).toHaveBeenCalledTimes(2);
    expect(apiMocks.searchMembers).toHaveBeenLastCalledWith("Ali", {});
  });

  it("shows matching results and keeps the query text", async () => {
    vi.useFakeTimers();
    apiMocks.searchMembers.mockImplementation((q: string) =>
      Promise.resolve(q === "Ali" ? [ALI] : []),
    );
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });

    render(<SearchPage />);

    expect(await screen.findByText("No family members yet")).toBeInTheDocument();

    const search = screen.getByRole("textbox", { name: "Search by name" });
    await user.type(search, "Ali");
    vi.advanceTimersByTime(300);

    expect(await screen.findByText("Ali Khan")).toBeInTheDocument();
    expect(search).toHaveValue("Ali");
  });

  it("retries after a failed directory read", async () => {
    apiMocks.searchMembers
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "raw failure"))
      .mockResolvedValueOnce([ALI]);
    const user = userEvent.setup();

    render(<SearchPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Directory unavailable");
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Ali Khan")).toBeInTheDocument();
    expect(apiMocks.searchMembers).toHaveBeenCalledTimes(2);
  });
});

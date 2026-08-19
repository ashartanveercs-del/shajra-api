import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "@/lib/http";

const apiMocks = vi.hoisted(() => ({ searchMembers: vi.fn() }));

vi.mock("@/lib/api", () => ({ searchMembers: apiMocks.searchMembers }));

import SearchPage from "./page";

const ALI = {
  id: "member-1",
  FullName: "Ali Khan",
  CurrentCity: "A very long city and district name that needs to wrap",
  Branch: "Khan",
  Generation: 3,
};

async function finishDebounce() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(300);
  });
  await act(async () => {
    await Promise.resolve();
  });
}

describe("SearchPage", () => {
  beforeEach(() => {
    apiMocks.searchMembers.mockReset();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("gives every directory filter a persistent accessible name without enumerating members", () => {
    apiMocks.searchMembers.mockResolvedValue([]);

    render(<SearchPage />);

    expect(screen.getByRole("textbox", { name: "City" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Branch" })).toBeInTheDocument();
    expect(screen.getByRole("spinbutton", { name: "Generation" })).toBeInTheDocument();
    expect(apiMocks.searchMembers).not.toHaveBeenCalled();
  });

  it("does not search empty or one-character input", async () => {
    vi.useFakeTimers();
    apiMocks.searchMembers.mockResolvedValue([]);

    render(<SearchPage />);

    expect(screen.getByText("Search by name or filter")).toBeInTheDocument();
    const search = screen.getByRole("searchbox", { name: "Search by name" });
    fireEvent.change(search, { target: { value: "A" } });
    await finishDebounce();

    expect(apiMocks.searchMembers).not.toHaveBeenCalled();
    expect(screen.getByText("Enter at least 2 characters or add a filter.")).toBeVisible();
  });

  it("searches through the endpoint after a two-character debounced query", async () => {
    vi.useFakeTimers();
    apiMocks.searchMembers.mockResolvedValue([]);

    render(<SearchPage />);
    const search = screen.getByRole("searchbox", { name: "Search by name" });
    fireEvent.change(search, { target: { value: "Ali" } });

    expect(apiMocks.searchMembers).not.toHaveBeenCalled();
    await finishDebounce();

    expect(apiMocks.searchMembers).toHaveBeenCalledOnce();
    expect(apiMocks.searchMembers).toHaveBeenLastCalledWith("Ali", {});
  });

  it("allows a meaningful filter without a name query", async () => {
    apiMocks.searchMembers.mockResolvedValue([]);
    const user = userEvent.setup();

    render(<SearchPage />);
    await user.type(screen.getByRole("textbox", { name: "City" }), "Lahore");

    await waitFor(() => {
      expect(apiMocks.searchMembers).toHaveBeenLastCalledWith("", { city: "Lahore" });
    });
  });

  it("shows matching results, retains the query, and wraps narrow metadata", async () => {
    vi.useFakeTimers();
    apiMocks.searchMembers.mockResolvedValue([ALI]);

    render(<SearchPage />);
    const search = screen.getByRole("searchbox", { name: "Search by name" });
    fireEvent.change(search, { target: { value: "Ali" } });
    await finishDebounce();

    const result = screen.getByRole("link", { name: /Ali Khan/ });
    expect(search).toHaveValue("Ali");
    expect(result).toHaveClass("min-w-0");
    expect(screen.getByText(ALI.CurrentCity).parentElement).toHaveClass("flex-wrap");
  });

  it("does not expose public contact values or contact actions", async () => {
    vi.useFakeTimers();
    apiMocks.searchMembers.mockResolvedValue([
      {
        ...ALI,
        Email: "private@example.com",
        PhoneNumber: "+1 202 555 0114",
      },
    ]);
    const { container } = render(<SearchPage />);

    fireEvent.change(screen.getByRole("searchbox", { name: "Search by name" }), {
      target: { value: "Ali" },
    });
    await finishDebounce();
    expect(screen.getByText("Ali Khan")).toBeInTheDocument();

    expect(screen.queryByText("private@example.com")).not.toBeInTheDocument();
    expect(screen.queryByText("+1 202 555 0114")).not.toBeInTheDocument();
    expect(container.querySelector('a[href^="mailto:"]')).not.toBeInTheDocument();
    expect(container.querySelector('a[href*="wa.me"]')).not.toBeInTheDocument();
    expect(container.querySelector('[title*="private@example.com"]')).not.toBeInTheDocument();
    expect(container.querySelector('[title*="202 555"]')).not.toBeInTheDocument();
  });

  it("retries only after a failed active search", async () => {
    apiMocks.searchMembers
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "raw failure"))
      .mockResolvedValueOnce([ALI]);
    const user = userEvent.setup();

    render(<SearchPage />);
    await user.type(screen.getByRole("searchbox", { name: "Search by name" }), "Ali");

    expect(await screen.findByRole("alert")).toHaveTextContent("Directory unavailable");
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("Ali Khan")).toBeInTheDocument();
    expect(apiMocks.searchMembers).toHaveBeenCalledTimes(2);
  });
});

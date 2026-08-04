import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "@/lib/http";

const apiMocks = vi.hoisted(() => ({
  fetchTree: vi.fn(),
  submitDirectForm: vi.fn(),
  uploadImage: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import TreePage from "./page";

describe("TreePage load states", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "ResizeObserver",
      class ResizeObserver {
        observe() {}
        unobserve() {}
        disconnect() {}
      },
    );
    apiMocks.fetchTree.mockReset();
    apiMocks.submitDirectForm.mockReset();
    apiMocks.uploadImage.mockReset();
  });

  it("shows a typed tree failure and retries into the real empty state", async () => {
    apiMocks.fetchTree
      .mockRejectedValueOnce(
        new ApiProblem(503, "TREE_UNAVAILABLE", "Family records are temporarily unavailable."),
      )
      .mockResolvedValueOnce([]);
    const user = userEvent.setup();

    render(<TreePage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Tree unavailable");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Family records could not be loaded.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent(
      "Family records are temporarily unavailable.",
    );

    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("No family members yet")).toBeInTheDocument();
    expect(apiMocks.fetchTree).toHaveBeenCalledTimes(2);
  });

  it("keeps the suggestion dialog and edited values after a rejected write", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      { id: "member-1", FullName: "Ali Khan", Gender: "Male", children: [] },
    ]);
    apiMocks.submitDirectForm.mockRejectedValue(
      new ApiProblem(503, "PUBLIC_WRITES_DISABLED", "raw write detail secret-token"),
    );
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    const user = userEvent.setup();

    render(<TreePage />);
    await user.click(await screen.findByRole("button", { name: "Suggest Edit" }));
    const nameInput = screen.getByRole("textbox", { name: "Full Name" });
    await user.clear(nameInput);
    await user.type(nameInput, "Ali Khan Updated");
    await user.click(screen.getByRole("button", { name: "Submit Suggestion" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Submissions are temporarily unavailable.",
    );
    expect(screen.getByRole("dialog", { name: "Suggest an Update" })).toBeInTheDocument();
    expect(nameInput).toHaveValue("Ali Khan Updated");
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret-token");
    expect(alertSpy).not.toHaveBeenCalled();
    alertSpy.mockRestore();
  });
});

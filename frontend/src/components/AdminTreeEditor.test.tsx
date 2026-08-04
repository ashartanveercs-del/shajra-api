import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "@/lib/http";

const apiMocks = vi.hoisted(() => ({
  fetchTree: vi.fn(),
  adminUpdateMember: vi.fn(),
  adminDeleteMember: vi.fn(),
  adminCreateMember: vi.fn(),
  uploadImage: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import AdminTreeEditor from "./AdminTreeEditor";

describe("AdminTreeEditor reliability states", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
  });

  it("retries an unavailable tree into the legitimate empty state", async () => {
    apiMocks.fetchTree
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "raw tree detail"))
      .mockResolvedValueOnce([]);
    const user = userEvent.setup();

    render(<AdminTreeEditor token="admin-token" />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Editor tree unavailable");
    expect(screen.queryByText("No family members yet")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("No family members yet")).toBeInTheDocument();
    expect(apiMocks.fetchTree).toHaveBeenCalledTimes(2);
  });

  it("keeps the edit draft and media after a rejected save", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      {
        id: "member-1",
        FullName: "Ali Khan",
        Gender: "Male",
        ProfileImageUrl: "https://images.example/ali.jpg",
        children: [],
      },
    ]);
    apiMocks.adminUpdateMember.mockRejectedValue(
      new ApiProblem(503, "RELATIONSHIP_WRITES_DISABLED", "raw graph detail secret-token"),
    );
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    const user = userEvent.setup();

    render(<AdminTreeEditor token="admin-token" />);
    await user.click(await screen.findByRole("button", { name: "Edit Ali Khan" }));
    const fullName = screen.getByRole("textbox", { name: "Full Name" });
    await user.clear(fullName);
    await user.type(fullName, "Ali Khan Updated");
    await user.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Relationship editing is temporarily unavailable.",
    );
    expect(screen.getByRole("dialog", { name: "Edit Member Info" })).toBeInTheDocument();
    expect(fullName).toHaveValue("Ali Khan Updated");
    expect(screen.getByText("Image Uploaded")).toBeInTheDocument();
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret-token");
    expect(alertSpy).not.toHaveBeenCalled();
    alertSpy.mockRestore();
  });
});

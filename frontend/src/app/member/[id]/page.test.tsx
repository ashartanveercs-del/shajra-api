import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "@/lib/http";

const apiMocks = vi.hoisted(() => ({
  fetchMember: vi.fn(),
  fetchMembers: vi.fn(),
  fetchComments: vi.fn(),
  fetchAlbums: vi.fn(),
  postComment: vi.fn(),
  verifyEmail: vi.fn(),
  uploadAlbumPhoto: vi.fn(),
  uploadImage: vi.fn(),
}));

const navigationMocks = vi.hoisted(() => ({ id: "member-1" }));

vi.mock("next/navigation", () => ({ useParams: () => ({ id: navigationMocks.id }) }));
vi.mock("@/lib/api", () => apiMocks);

import MemberProfilePage from "./page";

const member = { id: "member-1", FullName: "Ali Khan", Biography: "Family historian" };

describe("MemberProfilePage load states", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    navigationMocks.id = "member-1";
    apiMocks.fetchMembers.mockResolvedValue([]);
    apiMocks.fetchComments.mockResolvedValue([]);
    apiMocks.fetchAlbums.mockResolvedValue([]);
  });

  it("retries an unavailable member instead of calling it missing", async () => {
    apiMocks.fetchMember
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "raw provider detail"))
      .mockResolvedValueOnce(member);
    const user = userEvent.setup();

    render(<MemberProfilePage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Member unavailable");
    expect(screen.queryByText("Member Not Found")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByRole("heading", { name: "Ali Khan" })).toBeInTheDocument();
    expect(apiMocks.fetchMember).toHaveBeenCalledTimes(2);
  });

  it("keeps a loaded profile visible when auxiliary reads fail", async () => {
    apiMocks.fetchMember.mockResolvedValue(member);
    apiMocks.fetchComments.mockRejectedValue(
      new ApiProblem(503, "REQUEST_FAILED", "raw comments detail"),
    );
    apiMocks.fetchAlbums.mockRejectedValue(
      new ApiProblem(503, "REQUEST_FAILED", "raw albums detail"),
    );

    render(<MemberProfilePage />);

    expect(await screen.findByRole("heading", { name: "Ali Khan" })).toBeInTheDocument();
    expect(screen.getByText("Some profile details are unavailable")).toBeInTheDocument();
    expect(screen.getByText("Comments unavailable")).toBeInTheDocument();
    expect(screen.getByText("Albums unavailable")).toBeInTheDocument();
    expect(screen.queryByText(/No comments yet/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/No photos added yet/i)).not.toBeInTheDocument();
  });

  it("does not let an older member request replace the current route", async () => {
    let resolveFirst: ((value: typeof member) => void) | undefined;
    apiMocks.fetchMember
      .mockImplementationOnce(
        () =>
          new Promise<typeof member>((resolve) => {
            resolveFirst = resolve;
          }),
      )
      .mockResolvedValueOnce({ ...member, id: "member-2", FullName: "Sara Khan" });

    const view = render(<MemberProfilePage />);
    navigationMocks.id = "member-2";
    view.rerender(<MemberProfilePage />);

    expect(await screen.findByRole("heading", { name: "Sara Khan" })).toBeInTheDocument();

    await act(async () => {
      resolveFirst?.(member);
    });

    await waitFor(() => {
      expect(screen.getByRole("heading", { name: "Sara Khan" })).toBeInTheDocument();
    });
    expect(screen.queryByRole("heading", { name: "Ali Khan" })).not.toBeInTheDocument();
  });

  it("keeps comment fields after a safe inline posting failure", async () => {
    apiMocks.fetchMember.mockResolvedValue(member);
    apiMocks.verifyEmail.mockResolvedValue(true);
    apiMocks.postComment.mockRejectedValue(
      new ApiProblem(503, "REQUEST_FAILED", "raw comment detail secret-token"),
    );
    const user = userEvent.setup();

    render(<MemberProfilePage />);
    await screen.findByRole("heading", { name: "Ali Khan" });

    await user.type(screen.getByLabelText("Family email"), "family@example.com");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    await user.type(screen.getByLabelText("Your name"), "Ayesha");
    await user.type(screen.getByLabelText("Memory or story"), "A day worth remembering");
    await user.click(screen.getByRole("button", { name: "Post Comment" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The comment could not be posted.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret-token");
    expect(screen.getByLabelText("Your name")).toHaveValue("Ayesha");
    expect(screen.getByLabelText("Memory or story")).toHaveValue(
      "A day worth remembering",
    );
  });

  it("keeps the uploaded photo and caption after album submission fails", async () => {
    apiMocks.fetchMember.mockResolvedValue(member);
    apiMocks.uploadImage.mockResolvedValue({ url: "https://images.example/family.jpg" });
    apiMocks.uploadAlbumPhoto.mockRejectedValue(
      new ApiProblem(503, "PUBLIC_WRITES_DISABLED", "raw write detail"),
    );
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    const user = userEvent.setup();

    render(<MemberProfilePage />);
    await screen.findByRole("heading", { name: "Ali Khan" });
    await user.click(screen.getByRole("button", { name: "Add Photo" }));
    await user.upload(
      screen.getByLabelText("Choose album photo"),
      new File(["photo"], "family.jpg", { type: "image/jpeg" }),
    );
    await user.type(screen.getByLabelText("Photo caption"), "Family gathering");
    await user.click(screen.getByRole("button", { name: "Add to Album" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Submissions are temporarily unavailable.",
    );
    expect(screen.getByLabelText("Photo caption")).toHaveValue("Family gathering");
    expect(screen.getByText("Photo ready")).toBeInTheDocument();
    expect(alertSpy).not.toHaveBeenCalled();
    alertSpy.mockRestore();
  });

  it("keeps a 404 as a not-found state without retry", async () => {
    apiMocks.fetchMember.mockRejectedValue(
      new ApiProblem(404, "REQUEST_FAILED", "raw missing detail"),
    );

    render(<MemberProfilePage />);

    expect(await screen.findByText("Member Not Found")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });
});

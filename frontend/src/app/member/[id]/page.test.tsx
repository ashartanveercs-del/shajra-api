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

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

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

  it("does not render a member's private contact fields or contact actions", async () => {
    apiMocks.fetchMember.mockResolvedValue({
      ...member,
      Email: "private@example.com",
      PhoneNumber: "+1 202 555 0114",
    });
    const { container } = render(<MemberProfilePage />);

    expect(await screen.findByRole("heading", { name: "Ali Khan" })).toBeInTheDocument();
    expect(screen.queryByText("private@example.com")).not.toBeInTheDocument();
    expect(screen.queryByText("+1 202 555 0114")).not.toBeInTheDocument();
    expect(container.querySelector('a[href^="mailto:"]')).not.toBeInTheDocument();
    expect(container.querySelector('a[href*="wa.me"]')).not.toBeInTheDocument();
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

  it("immediately hides the old profile while a new member route is pending", async () => {
    const secondMember = deferred<typeof member>();
    apiMocks.fetchMember
      .mockResolvedValueOnce(member)
      .mockImplementationOnce(() => secondMember.promise);

    const view = render(<MemberProfilePage />);
    expect(await screen.findByRole("heading", { name: "Ali Khan" })).toBeInTheDocument();

    navigationMocks.id = "member-2";
    view.rerender(<MemberProfilePage />);

    expect(screen.getByText("Loading member profile")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Ali Khan" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Add Photo" })).not.toBeInTheDocument();

    await act(async () => {
      secondMember.resolve({ ...member, id: "member-2", FullName: "Sara Khan" });
    });
    expect(await screen.findByRole("heading", { name: "Sara Khan" })).toBeInTheDocument();
  });

  it("does not carry an album draft to a different member", async () => {
    apiMocks.fetchMember
      .mockResolvedValueOnce(member)
      .mockResolvedValueOnce({ ...member, id: "member-2", FullName: "Sara Khan" });
    const user = userEvent.setup();

    const view = render(<MemberProfilePage />);
    await screen.findByRole("heading", { name: "Ali Khan" });
    await user.click(screen.getByRole("button", { name: "Add Photo" }));
    await user.type(screen.getByLabelText("Photo caption"), "Ali's family gathering");

    navigationMocks.id = "member-2";
    view.rerender(<MemberProfilePage />);

    await screen.findByRole("heading", { name: "Sara Khan" });
    await user.click(screen.getByRole("button", { name: "Add Photo" }));
    expect(screen.getByLabelText("Photo caption")).toHaveValue("");
  });

  it("retries comments locally while retaining an uploaded album draft", async () => {
    apiMocks.fetchMember.mockResolvedValue(member);
    apiMocks.fetchComments
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "raw comments detail"))
      .mockResolvedValueOnce([
        {
          id: "comment-1",
          CommentText: "A remembered story",
          AuthorName: "Ayesha",
          MemberRecordId: "member-1",
        },
      ]);
    apiMocks.uploadImage.mockResolvedValue({ url: "https://images.example/family.jpg" });
    const user = userEvent.setup();

    render(<MemberProfilePage />);
    await screen.findByRole("heading", { name: "Ali Khan" });
    await user.click(screen.getByRole("button", { name: "Add Photo" }));
    await user.upload(
      screen.getByLabelText("Choose album photo"),
      new File(["photo"], "family.jpg", { type: "image/jpeg" }),
    );
    await user.type(screen.getByLabelText("Photo caption"), "Family gathering");

    await user.click(screen.getByRole("button", { name: "Retry comments" }));

    expect(await screen.findByText("A remembered story")).toBeInTheDocument();
    expect(screen.getByLabelText("Photo caption")).toHaveValue("Family gathering");
    expect(screen.getByText("Photo ready")).toBeInTheDocument();
    expect(apiMocks.fetchMember).toHaveBeenCalledTimes(1);
    expect(apiMocks.fetchMembers).toHaveBeenCalledTimes(1);
    expect(apiMocks.fetchComments).toHaveBeenCalledTimes(2);
    expect(apiMocks.fetchAlbums).toHaveBeenCalledTimes(1);
  });

  it("retries relationship and album reads through only their owned GETs", async () => {
    apiMocks.fetchMember.mockResolvedValue(member);
    apiMocks.fetchMembers
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "raw relationships detail"))
      .mockResolvedValueOnce([]);
    apiMocks.fetchAlbums
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "raw albums detail"))
      .mockResolvedValueOnce([]);
    const user = userEvent.setup();

    render(<MemberProfilePage />);
    await screen.findByRole("heading", { name: "Ali Khan" });

    await user.click(screen.getByRole("button", { name: "Retry relationships" }));
    await waitFor(() => {
      expect(screen.queryByText("Relationships unavailable")).not.toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: "Retry albums" }));
    await waitFor(() => {
      expect(screen.queryByText("Albums unavailable")).not.toBeInTheDocument();
    });

    expect(screen.getByRole("heading", { name: "Ali Khan" })).toBeInTheDocument();
    expect(apiMocks.fetchMember).toHaveBeenCalledTimes(1);
    expect(apiMocks.fetchMembers).toHaveBeenCalledTimes(2);
    expect(apiMocks.fetchComments).toHaveBeenCalledTimes(1);
    expect(apiMocks.fetchAlbums).toHaveBeenCalledTimes(2);
  });

  it("does not verify an edited email from an older deferred response", async () => {
    const verification = deferred<boolean>();
    apiMocks.fetchMember.mockResolvedValue(member);
    apiMocks.verifyEmail.mockImplementation(() => verification.promise);
    const user = userEvent.setup();

    render(<MemberProfilePage />);
    await screen.findByRole("heading", { name: "Ali Khan" });
    const emailInput = screen.getByLabelText("Family email");
    await user.type(emailInput, "First@Example.com");
    await user.click(screen.getByRole("button", { name: "Verify" }));
    await user.clear(emailInput);
    await user.type(emailInput, "second@example.com");

    await act(async () => {
      verification.resolve(true);
    });

    expect(screen.queryByText("Email verified. You can now post comments.")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Family email")).toHaveValue("second@example.com");
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

  it("retries a failed album upload with the same selected file", async () => {
    apiMocks.fetchMember.mockResolvedValue(member);
    apiMocks.uploadImage
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "raw upload detail"))
      .mockResolvedValueOnce({ url: "https://images.example/family.jpg" });
    const user = userEvent.setup();
    const file = new File(["photo"], "family.jpg", { type: "image/jpeg" });

    render(<MemberProfilePage />);
    await screen.findByRole("heading", { name: "Ali Khan" });
    await user.click(screen.getByRole("button", { name: "Add Photo" }));
    await user.type(screen.getByLabelText("Photo caption"), "Family gathering");
    await user.upload(screen.getByLabelText("Choose album photo"), file);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The photo could not be uploaded.",
    );
    await user.click(screen.getByRole("button", { name: "Retry Upload" }));

    expect(await screen.findByText("Photo ready")).toBeInTheDocument();
    expect(screen.getByLabelText("Photo caption")).toHaveValue("Family gathering");
    expect(apiMocks.uploadImage).toHaveBeenNthCalledWith(1, file);
    expect(apiMocks.uploadImage).toHaveBeenNthCalledWith(2, file);
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

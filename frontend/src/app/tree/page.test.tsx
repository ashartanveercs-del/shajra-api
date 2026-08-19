import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "@/lib/http";

const apiMocks = vi.hoisted(() => ({
  fetchTree: vi.fn(),
  submitDirectForm: vi.fn(),
  uploadImage: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);
vi.mock("@/components/FamilyTree3D", () => ({
  default: ({ tree }: { tree: Array<{ id: string }> }) => (
    <div aria-label="3D family tree" role="region">
      {tree.length} members in 3D
    </div>
  ),
}));

import TreePage from "./page";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((promiseResolve) => {
    resolve = promiseResolve;
  });
  return { promise, resolve };
}

describe("TreePage load states", () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
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

  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps 2D selected when 3D rendering is unavailable", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      { id: "member-1", FullName: "Ali Khan", Gender: "Male", children: [] },
    ]);
    vi.stubGlobal("WebGLRenderingContext", undefined);
    vi.stubGlobal("WebGL2RenderingContext", undefined);
    const user = userEvent.setup();

    render(<TreePage />);

    const twoDimensional = await screen.findByRole("button", { name: "2D" });
    const threeDimensional = screen.getByRole("button", { name: "3D" });
    expect(twoDimensional).toHaveAttribute("aria-pressed", "true");
    expect(threeDimensional).toHaveAttribute("aria-pressed", "false");
    expect(screen.queryByRole("region", { name: "3D family tree" })).not.toBeInTheDocument();

    await user.click(threeDimensional);

    expect(await screen.findByRole("status")).toHaveTextContent(
      "3D view is unavailable. The 2D family tree remains available.",
    );
    expect(twoDimensional).toHaveAttribute("aria-pressed", "true");
    expect(threeDimensional).toHaveAttribute("aria-pressed", "false");
  });

  it("loads the 3D enhancement only after a supported user selects it", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      { id: "member-1", FullName: "Ali Khan", Gender: "Male", children: [] },
    ]);
    vi.stubGlobal("WebGLRenderingContext", class WebGLRenderingContext {});
    vi.stubGlobal("WebGL2RenderingContext", undefined);
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    const user = userEvent.setup();

    render(<TreePage />);

    expect(await screen.findByRole("button", { name: "2D" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
    expect(screen.queryByRole("region", { name: "3D family tree" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "3D" }));

    expect(await screen.findByRole("region", { name: "3D family tree" })).toHaveTextContent(
      "1 members in 3D",
    );
    expect(screen.getByRole("button", { name: "3D" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "2D" })).toHaveAttribute("aria-pressed", "false");
  });

  it("hides the desktop add action through a responsive wrapper", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      { id: "member-1", FullName: "Ali Khan", Gender: "Male", children: [] },
    ]);

    render(<TreePage />);

    const addMember = await screen.findByRole("link", { name: "Add Family Member" });
    expect(addMember).not.toHaveClass("hidden");
    expect(addMember.parentElement).toHaveClass("hidden", "sm:block");
  });

  it("keeps the visible 2D tree for reduced-motion users", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      { id: "member-1", FullName: "Ali Khan", Gender: "Male", children: [] },
    ]);
    vi.stubGlobal("WebGLRenderingContext", class WebGLRenderingContext {});
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
    const user = userEvent.setup();

    render(<TreePage />);
    await user.click(await screen.findByRole("button", { name: "3D" }));

    expect(await screen.findByRole("status")).toHaveTextContent(
      "3D motion is disabled by your preferences. The 2D family tree remains available.",
    );
    expect(screen.getByRole("button", { name: "2D" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByText("Ali Khan")).toBeVisible();
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

  it("ignores an upload that finishes after closing and opening another member", async () => {
    const upload = deferred<{ url: string }>();
    apiMocks.fetchTree.mockResolvedValue([
      { id: "member-1", FullName: "Ali Khan", Gender: "Male", children: [] },
      { id: "member-2", FullName: "Sara Khan", Gender: "Female", children: [] },
    ]);
    apiMocks.uploadImage.mockImplementation(() => upload.promise);
    const user = userEvent.setup();

    render(<TreePage />);
    const editButtons = await screen.findAllByRole("button", { name: "Suggest Edit" });
    await user.click(editButtons[0]);
    await user.upload(
      screen.getByLabelText("Upload profile photo"),
      new File(["photo"], "ali.jpg", { type: "image/jpeg" }),
    );
    await user.click(screen.getByRole("button", { name: "Close suggestion" }));
    await user.click(editButtons[1]);

    await act(async () => {
      upload.resolve({ url: "https://images.example/ali.jpg" });
    });

    expect(screen.getByRole("dialog", { name: "Suggest an Update" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Full Name" })).toHaveValue("Sara Khan");
    expect(screen.queryByRole("img", { name: "Profile preview" })).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not let an old success timer close a reopened dialog", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      { id: "member-1", FullName: "Ali Khan", Gender: "Male", children: [] },
      { id: "member-2", FullName: "Sara Khan", Gender: "Female", children: [] },
    ]);
    apiMocks.submitDirectForm.mockResolvedValue({});
    const user = userEvent.setup();

    render(<TreePage />);
    const editButtons = await screen.findAllByRole("button", { name: "Suggest Edit" });
    await user.click(editButtons[0]);
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "Submit Suggestion" }));
    await act(async () => undefined);
    expect(screen.getByText("Suggestion Sent!")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close suggestion" }));
    fireEvent.click(editButtons[1]);
    act(() => {
      vi.advanceTimersByTime(3000);
    });

    expect(screen.getByRole("dialog", { name: "Suggest an Update" })).toBeInTheDocument();
    expect(screen.getByRole("textbox", { name: "Full Name" })).toHaveValue("Sara Khan");
    expect(screen.queryByText("Suggestion Sent!")).not.toBeInTheDocument();
  });

  it("retries a failed suggestion upload with the same selected file", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      { id: "member-1", FullName: "Ali Khan", Gender: "Male", children: [] },
    ]);
    apiMocks.uploadImage
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "raw upload detail"))
      .mockResolvedValueOnce({ url: "https://images.example/ali.jpg" });
    const user = userEvent.setup();
    const file = new File(["photo"], "ali.jpg", { type: "image/jpeg" });

    render(<TreePage />);
    await user.click(await screen.findByRole("button", { name: "Suggest Edit" }));
    const nameInput = screen.getByRole("textbox", { name: "Full Name" });
    await user.clear(nameInput);
    await user.type(nameInput, "Ali Khan Updated");
    await user.upload(screen.getByLabelText("Upload profile photo"), file);

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The photo could not be uploaded. Please try again.",
    );
    await user.click(screen.getByRole("button", { name: "Retry Upload" }));

    expect(await screen.findByRole("img", { name: "Profile preview" })).toBeInTheDocument();
    expect(nameInput).toHaveValue("Ali Khan Updated");
    expect(apiMocks.uploadImage).toHaveBeenNthCalledWith(1, file);
    expect(apiMocks.uploadImage).toHaveBeenNthCalledWith(2, file);
  });
});

import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
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

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

const relationshipCases = [
  ["Male with spouse", "Male", true, { FatherRecordId: "target", MotherRecordId: "spouse" }],
  ["Female with spouse", "Female", true, { MotherRecordId: "target", FatherRecordId: "spouse" }],
  ["Other with spouse", "Other", true, { MotherRecordId: "target", FatherRecordId: "spouse" }],
  ["missing gender with spouse", undefined, true, { MotherRecordId: "target", FatherRecordId: "spouse" }],
  ["Male without spouse", "Male", false, { FatherRecordId: "target" }],
  ["Female without spouse", "Female", false, { MotherRecordId: "target" }],
  ["Other without spouse", "Other", false, { FatherRecordId: "target" }],
  ["missing gender without spouse", undefined, false, { FatherRecordId: "target" }],
] as const;

function relationshipFields(payload: Record<string, unknown>) {
  return Object.fromEntries(
    ["FatherRecordId", "MotherRecordId"]
      .filter((field) => field in payload)
      .map((field) => [field, payload[field]]),
  );
}

describe("AdminTreeEditor reliability states", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
  });

  it.each(relationshipCases)(
    "preserves legacy relationship payloads for %s",
    async (_label, gender, hasSpouse, expected) => {
      const target = {
        id: "target",
        FullName: "Target Parent",
        ...(gender ? { Gender: gender } : {}),
        ...(hasSpouse
          ? { Spouse: { id: "spouse", FullName: "Target Spouse", Gender: "Female" } }
          : {}),
        children: [],
      };
      const tree = [
        { id: "dragged", FullName: "Dragged Child", Gender: "Male", children: [] },
        target,
      ];
      apiMocks.fetchTree.mockResolvedValue(tree);
      apiMocks.adminUpdateMember.mockResolvedValue({});
      apiMocks.adminCreateMember.mockResolvedValue({});
      const user = userEvent.setup();

      render(<AdminTreeEditor token="admin-token" />);

      const draggedCard = (await screen.findByRole("button", { name: "Edit Dragged Child" })).closest(
        "[draggable='true']",
      );
      const targetCard = screen.getByRole("button", { name: "Edit Target Parent" }).closest(
        "[draggable='true']",
      );
      expect(draggedCard).not.toBeNull();
      expect(targetCard).not.toBeNull();
      const data = new Map<string, string>();
      const dataTransfer = {
        setData: (type: string, value: string) => data.set(type, value),
        getData: (type: string) => data.get(type) ?? "",
        effectAllowed: "none",
        dropEffect: "none",
      };

      fireEvent.dragStart(draggedCard as Element, { dataTransfer });
      fireEvent.drop(targetCard as Element, { dataTransfer });

      await waitFor(() => expect(apiMocks.adminUpdateMember).toHaveBeenCalled());
      expect(relationshipFields(apiMocks.adminUpdateMember.mock.calls[0][2])).toEqual(expected);

      await user.click(screen.getByRole("button", { name: "Add child to Target Parent" }));
      await user.click(screen.getByRole("button", { name: "Create Member" }));
      await waitFor(() => expect(apiMocks.adminCreateMember).toHaveBeenCalled());
      expect(relationshipFields(apiMocks.adminCreateMember.mock.calls[0][1])).toEqual(expected);
    },
  );

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

  it("sends a spouse change as one server-side relationship command", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      {
        id: "member-a",
        FullName: "Member Alpha",
        Gender: "Male",
        SpouseRecordId: "member-b",
        Spouse: { id: "member-b", FullName: "Member Beta", Gender: "Female" },
        children: [],
      },
      { id: "member-c", FullName: "Member Gamma", Gender: "Female", children: [] },
    ]);
    apiMocks.adminUpdateMember.mockResolvedValue({});
    const user = userEvent.setup();

    render(<AdminTreeEditor token="admin-token" />);
    await user.click(await screen.findByRole("button", { name: "Edit Member Alpha" }));
    await user.selectOptions(
      screen.getByRole("combobox", { name: "Spouse (link to existing member)" }),
      "member-c",
    );
    await user.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "Edit Member Info" })).not.toBeInTheDocument();
    });
    expect(apiMocks.adminUpdateMember).toHaveBeenCalledOnce();
    expect(apiMocks.adminUpdateMember).toHaveBeenCalledWith(
      "admin-token",
      "member-a",
      expect.objectContaining({ SpouseRecordId: "member-c", SpouseName: "" }),
    );
  });

  it("reports mutation undo metadata to the admin dashboard", async () => {
    const result = {
      id: "member-a",
      undoAvailable: false,
      undoWarning: "The change was saved without durable history.",
    };
    apiMocks.fetchTree.mockResolvedValue([
      { id: "member-a", FullName: "Member Alpha", Gender: "Male", children: [] },
    ]);
    apiMocks.adminUpdateMember.mockResolvedValue(result);
    const onUpdated = vi.fn();
    const user = userEvent.setup();

    render(<AdminTreeEditor token="admin-token" onUpdated={onUpdated} />);
    await user.click(await screen.findByRole("button", { name: "Edit Member Alpha" }));
    await user.click(screen.getByRole("button", { name: "Save Changes" }));

    await waitFor(() => expect(onUpdated).toHaveBeenCalledWith(result));
  });

  it("offers only real gender-matched non-descendants as structural parents", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      {
        id: "edited",
        FullName: "Edited Member",
        Gender: "Male",
        children: [
          {
            id: "son",
            FullName: "Descendant Son",
            Gender: "Male",
            FatherRecordId: "edited",
            children: [
              {
                id: "granddaughter",
                FullName: "Descendant Granddaughter",
                Gender: "Female",
                FatherRecordId: "son",
                children: [],
              },
            ],
          },
        ],
      },
      { id: "valid-father", FullName: "Valid Father", Gender: "Male", children: [] },
      { id: "valid-mother", FullName: "Valid Mother", Gender: "Female", children: [] },
      { id: "unknown-gender", FullName: "Unknown Gender", children: [] },
      {
        id: "__name__edited__father",
        FullName: "Name-only Father",
        Gender: "Male",
        IsPlaceholder: true,
        children: [],
      },
      {
        id: "placeholder-mother",
        FullName: "Placeholder Mother",
        Gender: "Female",
        IsPlaceholder: true,
        children: [],
      },
    ]);
    const user = userEvent.setup();

    render(<AdminTreeEditor token="admin-token" />);
    await user.click(await screen.findByRole("button", { name: "Edit Edited Member" }));

    const fatherOptions = within(
      screen.getByRole("combobox", { name: "Structural Father" }),
    ).getAllByRole("option");
    const motherOptions = within(
      screen.getByRole("combobox", { name: "Structural Mother" }),
    ).getAllByRole("option");

    expect(fatherOptions.map((option) => option.textContent)).toEqual([
      "No father",
      "Valid Father",
    ]);
    expect(motherOptions.map((option) => option.textContent)).toEqual([
      "No mother",
      "Valid Mother",
    ]);
  });

  it("keeps linked and name-only spouse inputs mutually exclusive", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      {
        id: "member-a",
        FullName: "Member Alpha",
        Gender: "Male",
        SpouseRecordId: "member-b",
        SpouseName: "Member Beta",
        Spouse: { id: "member-b", FullName: "Member Beta", Gender: "Female" },
        children: [],
      },
      { id: "member-c", FullName: "Member Gamma", Gender: "Female", children: [] },
    ]);
    apiMocks.adminUpdateMember.mockResolvedValue({});
    const user = userEvent.setup();

    render(<AdminTreeEditor token="admin-token" />);
    await user.click(await screen.findByRole("button", { name: "Edit Member Alpha" }));

    const spouseSelect = screen.getByRole("combobox", {
      name: "Spouse (link to existing member)",
    });
    const spouseName = screen.getByRole("textbox", { name: "Spouse Name (if not in tree)" });

    expect(spouseName).toBeDisabled();
    await user.selectOptions(spouseSelect, "member-c");
    expect(spouseName).toHaveValue("");
    expect(spouseName).toBeDisabled();

    await user.selectOptions(spouseSelect, "");
    expect(spouseName).toBeEnabled();
    await user.type(spouseName, "Unlisted Partner");
    await user.click(screen.getByRole("button", { name: "Save Changes" }));

    expect(apiMocks.adminUpdateMember).toHaveBeenCalledWith(
      "admin-token",
      "member-a",
      expect.objectContaining({
        SpouseRecordId: "",
        SpouseName: "Unlisted Partner",
      }),
    );
  });

  it("unlinks spouses with one server-side relationship command", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      {
        id: "member-a",
        FullName: "Member Alpha",
        Gender: "Male",
        SpouseRecordId: "member-b",
        Spouse: { id: "member-b", FullName: "Member Beta", Gender: "Female" },
        children: [],
      },
    ]);
    apiMocks.adminUpdateMember.mockResolvedValue({});
    const user = userEvent.setup();

    render(<AdminTreeEditor token="admin-token" />);
    await user.click(
      await screen.findByRole("button", { name: "Unlink Member Alpha and Member Beta" }),
    );

    await waitFor(() => expect(apiMocks.fetchTree).toHaveBeenCalledTimes(2));
    expect(apiMocks.adminUpdateMember).toHaveBeenCalledOnce();
    expect(apiMocks.adminUpdateMember).toHaveBeenCalledWith("admin-token", "member-a", {
      SpouseRecordId: "",
      SpouseName: "",
    });
  });

  it("ignores a late upload result after another member draft opens", async () => {
    const upload = deferred<{ url: string }>();
    apiMocks.fetchTree.mockResolvedValue([
      { id: "member-a", FullName: "Member Alpha", Gender: "Male", children: [] },
      { id: "member-b", FullName: "Member Beta", Gender: "Female", children: [] },
    ]);
    apiMocks.uploadImage.mockImplementation(() => upload.promise);
    const user = userEvent.setup();

    render(<AdminTreeEditor token="admin-token" />);

    await user.click(await screen.findByRole("button", { name: "Edit Member Alpha" }));
    await user.upload(
      screen.getByLabelText("Profile Image"),
      new File(["image"], "alpha.png", { type: "image/png" }),
    );
    await user.click(screen.getByRole("button", { name: "Close member editor" }));
    await user.click(screen.getByRole("button", { name: "Edit Member Beta" }));

    upload.resolve({ url: "https://images.example/alpha.png" });

    await waitFor(() => expect(screen.getByRole("textbox", { name: "Full Name" })).toHaveValue("Member Beta"));
    expect(screen.queryByText("Image Uploaded")).not.toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("ignores a late upload failure after another member draft opens", async () => {
    const upload = deferred<{ url: string }>();
    apiMocks.fetchTree.mockResolvedValue([
      { id: "member-a", FullName: "Member Alpha", Gender: "Male", children: [] },
      { id: "member-b", FullName: "Member Beta", Gender: "Female", children: [] },
    ]);
    apiMocks.uploadImage.mockImplementation(() => upload.promise);
    const user = userEvent.setup();

    render(<AdminTreeEditor token="admin-token" />);

    await user.click(await screen.findByRole("button", { name: "Edit Member Alpha" }));
    await user.upload(
      screen.getByLabelText("Profile Image"),
      new File(["image"], "alpha.png", { type: "image/png" }),
    );
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Edit Member Beta" }));

    upload.reject(new ApiProblem(503, "REQUEST_FAILED", "old upload failed"));

    await waitFor(() => expect(screen.getByRole("textbox", { name: "Full Name" })).toHaveValue("Member Beta"));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("does not let a late save close a newer member draft", async () => {
    const save = deferred<void>();
    apiMocks.fetchTree.mockResolvedValue([
      { id: "member-a", FullName: "Member Alpha", Gender: "Male", children: [] },
      { id: "member-b", FullName: "Member Beta", Gender: "Female", children: [] },
    ]);
    apiMocks.adminUpdateMember.mockImplementation(() => save.promise);
    const user = userEvent.setup();

    render(<AdminTreeEditor token="admin-token" />);

    await user.click(await screen.findByRole("button", { name: "Edit Member Alpha" }));
    await user.click(screen.getByRole("button", { name: "Save Changes" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(screen.getByRole("button", { name: "Edit Member Beta" }));

    save.resolve();

    await waitFor(() => expect(screen.getByRole("dialog", { name: "Edit Member Info" })).toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: "Full Name" })).toHaveValue("Member Beta");
  });

  it("does not let a late create close a newer member draft", async () => {
    const create = deferred<void>();
    apiMocks.fetchTree.mockResolvedValue([]);
    apiMocks.adminCreateMember.mockImplementation(() => create.promise);
    const user = userEvent.setup();

    render(<AdminTreeEditor token="admin-token" />);

    const invoker = await screen.findByRole("button", { name: /^Add New Member/ });
    await user.click(invoker);
    await user.type(screen.getByRole("textbox", { name: "Full Name" }), "First Draft");
    await user.click(screen.getByRole("button", { name: "Create Member" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await user.click(invoker);
    await user.type(screen.getByRole("textbox", { name: "Full Name" }), "Second Draft");

    create.resolve();

    await waitFor(() => expect(screen.getByRole("dialog", { name: "Edit Member Info" })).toBeInTheDocument());
    expect(screen.getByRole("textbox", { name: "Full Name" })).toHaveValue("Second Draft");
  });

  it("contains modal focus, closes on Escape, and restores the invoker", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      { id: "member-1", FullName: "Ali Khan", Gender: "Male", children: [] },
    ]);
    const user = userEvent.setup();

    render(<AdminTreeEditor token="admin-token" />);

    const invoker = await screen.findByRole("button", { name: "Edit Ali Khan" });
    await user.click(invoker);
    const close = screen.getByRole("button", { name: "Close member editor" });
    await waitFor(() => expect(close).toHaveFocus());

    await user.tab({ shift: true });
    expect(screen.getByRole("button", { name: "Save Changes" })).toHaveFocus();
    await user.tab();
    expect(close).toHaveFocus();

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "Edit Member Info" })).not.toBeInTheDocument();
    expect(invoker).toHaveFocus();
  });

  it("keeps the editor frame viewport-relative without a short-screen minimum", async () => {
    apiMocks.fetchTree.mockResolvedValue([]);

    render(<AdminTreeEditor token="admin-token" />);

    const heading = await screen.findByRole("heading", { name: "Interactive Heritage Tree" });
    const frame = heading.parentElement?.parentElement;
    expect(frame).toHaveClass("h-[65vh]", "supports-[height:100dvh]:h-[65dvh]", "min-h-0");
    expect(frame).not.toHaveClass("min-h-[28rem]");
  });

  it("copies only supported string CardStyle fields from stored JSON", async () => {
    apiMocks.fetchTree.mockResolvedValue([
      {
        id: "styled-member",
        FullName: "Styled Member",
        CardStyle: JSON.stringify({
          badge: "LEGACY",
          badgeColor: 42,
          borderColor: "#123456",
          className: ["unsafe-class"],
          unsupported: "unsafe-class",
        }),
        children: [],
      },
      { id: "array-style", FullName: "Array Style", CardStyle: "[]", children: [] },
      { id: "bad-style", FullName: "Bad Style", CardStyle: "{bad json", children: [] },
    ]);

    render(<AdminTreeEditor token="admin-token" />);

    const styledCard = (await screen.findByRole("button", { name: "Edit Styled Member" })).closest(
      "[draggable='true']",
    );
    expect(styledCard).toHaveStyle({ borderColor: "#123456" });
    expect(styledCard).not.toHaveClass("unsafe-class");
    expect(screen.getByText("LEGACY")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit Array Style" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit Bad Style" })).toBeInTheDocument();
  });
});

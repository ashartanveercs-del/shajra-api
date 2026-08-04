import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "@/lib/http";

const apiMocks = vi.hoisted(() => ({
  adminLogin: vi.fn(),
  fetchPending: vi.fn(),
  approveSubmission: vi.fn(),
  rejectSubmission: vi.fn(),
  fetchMembers: vi.fn(),
  adminCreateMember: vi.fn(),
  adminDeleteMember: vi.fn(),
  adminFetchApprovedEmails: vi.fn(),
  adminAddApprovedEmail: vi.fn(),
  adminDeleteApprovedEmail: vi.fn(),
  adminFetchIntegrations: vi.fn(),
  adminUndo: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);
vi.mock("@/components/AdminTreeEditor", () => ({ default: () => null }));

import AdminPage from "./page";

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void;
  let reject!: (reason?: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

describe("AdminPage reliability states", () => {
  beforeEach(() => {
    Object.values(apiMocks).forEach((mock) => mock.mockReset());
    localStorage.clear();
    localStorage.setItem("shajra_admin_token", "admin-token");
    apiMocks.fetchPending.mockResolvedValue([]);
    apiMocks.fetchMembers.mockResolvedValue([]);
    apiMocks.adminFetchApprovedEmails.mockResolvedValue([]);
  });

  it("keeps successful dashboard data usable when one section fails", async () => {
    apiMocks.fetchPending.mockResolvedValue([
      { id: "submission-1", Status: "Pending", RawFullName: "Ali Pending" },
    ]);
    apiMocks.fetchMembers.mockRejectedValue(
      new ApiProblem(503, "REQUEST_FAILED", "raw member provider detail"),
    );
    const user = userEvent.setup();

    render(<AdminPage />);

    expect(await screen.findByText("Some dashboard data is unavailable")).toBeInTheDocument();
    expect(screen.getByText("Ali Pending")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Members/ }));
    expect(screen.getByText("Members unavailable")).toBeInTheDocument();
    expect(screen.queryByText("No members yet")).not.toBeInTheDocument();
    expect(apiMocks.adminLogin).not.toHaveBeenCalled();
  });

  it("retries only the failed section while retaining sibling dashboard data", async () => {
    const membersRetry = deferred<Array<{ id: string; FullName: string }>>();
    apiMocks.fetchPending.mockResolvedValue([
      { id: "submission-1", Status: "Pending", RawFullName: "Ali Pending" },
    ]);
    apiMocks.fetchMembers
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "member read failed"))
      .mockImplementationOnce(() => membersRetry.promise);
    apiMocks.adminFetchApprovedEmails.mockResolvedValue([
      { id: "email-1", Email: "family@example.com", Name: "Family" },
    ]);
    const user = userEvent.setup();

    render(<AdminPage />);

    await user.click(await screen.findByRole("button", { name: /^Members/ }));
    const retry = screen.getByRole("button", { name: "Retry" });
    await user.click(retry);

    expect(retry).toBeDisabled();
    expect(apiMocks.fetchMembers).toHaveBeenCalledTimes(2);
    expect(apiMocks.fetchPending).toHaveBeenCalledTimes(1);
    expect(apiMocks.adminFetchApprovedEmails).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: /^Pending/ }));
    expect(screen.getByText("Ali Pending")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^Approved Emails/ }));
    expect(screen.getByText("family@example.com")).toBeInTheDocument();

    membersRetry.resolve([{ id: "member-1", FullName: "Sara Khan" }]);
    await user.click(screen.getByRole("button", { name: /^Members/ }));
    expect(await screen.findByText("Sara Khan")).toBeInTheDocument();
  });

  it("settles concurrent section retries without leaving either section stuck", async () => {
    const membersRetry = deferred<Array<{ id: string; FullName: string }>>();
    const emailsRetry = deferred<Array<{ id: string; Email: string; Name: string }>>();
    apiMocks.fetchPending.mockResolvedValue([
      { id: "submission-1", Status: "Pending", RawFullName: "Ali Pending" },
    ]);
    apiMocks.fetchMembers
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "member read failed"))
      .mockImplementationOnce(() => membersRetry.promise);
    apiMocks.adminFetchApprovedEmails
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "email read failed"))
      .mockImplementationOnce(() => emailsRetry.promise);
    const user = userEvent.setup();

    render(<AdminPage />);

    await user.click(await screen.findByRole("button", { name: /^Members/ }));
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await user.click(screen.getByRole("button", { name: /^Approved Emails/ }));
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();

    membersRetry.resolve([{ id: "member-1", FullName: "Sara Khan" }]);
    await user.click(screen.getByRole("button", { name: /^Members/ }));
    expect(await screen.findByText("Sara Khan")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /^Approved Emails/ }));
    expect(screen.getByRole("button", { name: "Retry" })).toBeDisabled();
    emailsRetry.resolve([
      { id: "email-1", Email: "family@example.com", Name: "Family" },
    ]);
    expect(await screen.findByText("family@example.com")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();

    apiMocks.fetchMembers.mockRejectedValueOnce(
      new ApiProblem(503, "REQUEST_FAILED", "member reload failed"),
    );
    apiMocks.adminFetchApprovedEmails.mockRejectedValueOnce(
      new ApiProblem(503, "REQUEST_FAILED", "email reload failed"),
    );
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(apiMocks.fetchMembers).toHaveBeenCalledTimes(3));
    await user.click(screen.getByRole("button", { name: /^Members/ }));
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: /^Approved Emails/ }));
    expect(screen.getByRole("button", { name: "Retry" })).toBeEnabled();

    await user.click(screen.getByRole("button", { name: /^Pending/ }));
    expect(screen.getByText("Ali Pending")).toBeInTheDocument();
    expect(apiMocks.fetchPending).toHaveBeenCalledTimes(2);
    expect(apiMocks.fetchMembers).toHaveBeenCalledTimes(3);
    expect(apiMocks.adminFetchApprovedEmails).toHaveBeenCalledTimes(3);
  });

  it("resets a deferred refresh when logout starts a newer session", async () => {
    const oldRefresh = deferred<Array<{ id: string; FullName: string }>>();
    apiMocks.fetchMembers
      .mockResolvedValueOnce([{ id: "old-member", FullName: "Old Session" }])
      .mockImplementationOnce(() => oldRefresh.promise)
      .mockResolvedValue([{ id: "new-member", FullName: "New Session" }]);
    apiMocks.adminLogin.mockResolvedValue("new-token");
    const user = userEvent.setup();

    render(<AdminPage />);

    await screen.findByRole("button", { name: /^Members/ });
    await user.click(screen.getByRole("button", { name: "Refresh" }));
    await waitFor(() => expect(apiMocks.fetchMembers).toHaveBeenCalledTimes(2));
    await user.click(screen.getByRole("button", { name: "Logout" }));
    await user.type(screen.getByRole("textbox", { name: "Username" }), "admin");
    await user.type(screen.getByLabelText("Password"), "password");
    await user.click(screen.getByRole("button", { name: "Sign In" }));
    await user.click(await screen.findByRole("button", { name: /^Members/ }));

    expect(await screen.findByText("New Session")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Refresh" })).toBeEnabled();

    oldRefresh.resolve([{ id: "stale-refresh", FullName: "Stale Refresh" }]);
    await waitFor(() => expect(screen.queryByText("Stale Refresh")).not.toBeInTheDocument());
    expect(screen.getByText("New Session")).toBeInTheDocument();
  });

  it("ignores an old mutation completion after logout and a second login", async () => {
    const oldUndo = deferred<void>();
    apiMocks.fetchMembers
      .mockResolvedValueOnce([{ id: "old-member", FullName: "Old Session" }])
      .mockResolvedValueOnce([{ id: "new-member", FullName: "New Session" }])
      .mockResolvedValue([{ id: "stale-member", FullName: "Stale Mutation Refresh" }]);
    apiMocks.adminUndo.mockImplementationOnce(() => oldUndo.promise);
    apiMocks.adminLogin.mockResolvedValue("new-token");
    const user = userEvent.setup();

    render(<AdminPage />);

    await screen.findByRole("button", { name: /^Members/ });
    await user.click(screen.getByRole("button", { name: "Undo Last Change" }));
    await user.click(screen.getByRole("button", { name: "Logout" }));
    await user.type(screen.getByRole("textbox", { name: "Username" }), "admin");
    await user.type(screen.getByLabelText("Password"), "password");
    await user.click(screen.getByRole("button", { name: "Sign In" }));
    await user.click(await screen.findByRole("button", { name: /^Members/ }));
    expect(await screen.findByText("New Session")).toBeInTheDocument();

    oldUndo.resolve();

    await waitFor(() => expect(apiMocks.fetchMembers).toHaveBeenCalledTimes(2));
    expect(screen.queryByText("Stale Mutation Refresh")).not.toBeInTheDocument();
    expect(screen.getByText("New Session")).toBeInTheDocument();
  });

  it("guards login reentry while the first login is pending", async () => {
    localStorage.clear();
    const login = deferred<string>();
    apiMocks.adminLogin.mockImplementation(() => login.promise);
    const user = userEvent.setup();

    render(<AdminPage />);

    await user.type(screen.getByRole("textbox", { name: "Username" }), "admin");
    const password = screen.getByLabelText("Password");
    await user.type(password, "password");
    await user.click(screen.getByRole("button", { name: "Sign In" }));
    await user.type(password, "{Enter}");

    expect(apiMocks.adminLogin).toHaveBeenCalledTimes(1);

    login.resolve("new-token");
    expect(await screen.findByText("Admin Dashboard")).toBeInTheDocument();
  });

  it("shows read-only integration booleans without heal or secret controls", async () => {
    apiMocks.adminFetchIntegrations.mockResolvedValue({
      groqConfigured: false,
      cloudinaryConfigured: true,
      coordinationConfigured: true,
    });
    const user = userEvent.setup();

    render(<AdminPage />);
    await user.click(await screen.findByRole("button", { name: "Integrations" }));

    expect(await screen.findByText("Groq")).toBeInTheDocument();
    const integrationFrame = screen.getByRole("heading", { name: "Integration Status" }).parentElement;
    expect(integrationFrame).toHaveClass("rounded-lg");
    expect(integrationFrame).not.toHaveClass("heritage-card");
    expect(screen.getByText("Not configured")).toBeInTheDocument();
    expect(screen.getAllByText("Configured")).toHaveLength(2);
    expect(screen.queryByRole("button", { name: /Heal Graph/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Save Settings/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/API Key/i)).not.toBeInTheDocument();
  });

  it("names pending and member action buttons for their records", async () => {
    apiMocks.fetchPending.mockResolvedValue([
      { id: "submission-1", Status: "Pending", RawFullName: "Ali Pending" },
    ]);
    apiMocks.fetchMembers.mockResolvedValue([{ id: "member-1", FullName: "Sara Khan" }]);
    const user = userEvent.setup();

    render(<AdminPage />);

    expect(await screen.findByRole("button", { name: "View Ali Pending details" })).toHaveAttribute("type", "button");
    expect(screen.getByRole("button", { name: "Approve Ali Pending" })).toHaveAttribute("type", "button");
    expect(screen.getByRole("button", { name: "Reject Ali Pending" })).toHaveAttribute("type", "button");
    await user.click(screen.getByRole("button", { name: /^Members/ }));
    expect(screen.getByRole("button", { name: "Delete Sara Khan" })).toHaveAttribute("type", "button");
  });

  it("keeps approved-email inputs after a rejected write", async () => {
    apiMocks.adminAddApprovedEmail.mockRejectedValue(
      new ApiProblem(503, "PUBLIC_WRITES_DISABLED", "raw write detail secret-token"),
    );
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    const user = userEvent.setup();

    render(<AdminPage />);
    await user.click(await screen.findByRole("button", { name: /^Approved Emails/ }));
    await user.type(screen.getByRole("textbox", { name: "Name" }), "Ayesha");
    await user.type(screen.getByRole("textbox", { name: "Email" }), "ayesha@example.com");
    await user.click(screen.getByRole("button", { name: "Add Email" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Submissions are temporarily unavailable.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret-token");
    expect(screen.getByRole("textbox", { name: "Name" })).toHaveValue("Ayesha");
    expect(screen.getByRole("textbox", { name: "Email" })).toHaveValue(
      "ayesha@example.com",
    );
    expect(alertSpy).not.toHaveBeenCalled();
    alertSpy.mockRestore();
  });

  it("keeps add-member fields after a rejected write", async () => {
    apiMocks.adminCreateMember.mockRejectedValue(
      new ApiProblem(503, "PUBLIC_WRITES_DISABLED", "raw member write detail"),
    );
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => undefined);
    const user = userEvent.setup();

    render(<AdminPage />);
    await user.click(await screen.findByRole("button", { name: "Add Member" }));
    const fullName = screen.getByRole("textbox", { name: /^Full Name/ });
    await user.type(fullName, "Sara Khan");
    await user.click(screen.getByRole("button", { name: "Create member record" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Submissions are temporarily unavailable.",
    );
    expect(fullName).toHaveValue("Sara Khan");
    expect(alertSpy).not.toHaveBeenCalled();
    alertSpy.mockRestore();
  });

  it("clears an earlier add-member success before a later attempt fails", async () => {
    apiMocks.adminCreateMember
      .mockResolvedValueOnce({ id: "member-1" })
      .mockRejectedValueOnce(
        new ApiProblem(503, "PUBLIC_WRITES_DISABLED", "raw member write detail"),
      );
    const user = userEvent.setup();

    render(<AdminPage />);
    await user.click(await screen.findByRole("button", { name: "Add Member" }));
    const fullName = screen.getByRole("textbox", { name: /^Full Name/ });
    await user.type(fullName, "First Member");
    await user.click(screen.getByRole("button", { name: "Create member record" }));
    expect(await screen.findByText("Member added successfully!")).toBeInTheDocument();

    await user.type(fullName, "Second Member");
    await user.click(screen.getByRole("button", { name: "Create member record" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Submissions are temporarily unavailable.",
    );
    expect(screen.queryByText("Member added successfully!")).not.toBeInTheDocument();
    expect(fullName).toHaveValue("Second Member");
  });
});

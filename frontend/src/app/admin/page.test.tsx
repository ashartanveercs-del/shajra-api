import { render, screen } from "@testing-library/react";
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
});

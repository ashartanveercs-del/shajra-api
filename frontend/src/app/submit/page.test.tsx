import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "@/lib/http";

const apiMocks = vi.hoisted(() => ({
  submitDirectForm: vi.fn(),
  uploadImage: vi.fn(),
}));

vi.mock("@/lib/api", () => apiMocks);

import SubmitPage from "./page";

describe("SubmitPage failure states", () => {
  beforeEach(() => {
    apiMocks.submitDirectForm.mockReset();
    apiMocks.uploadImage.mockReset();
  });

  it("keeps entered fields and redacts arbitrary photo upload failures", async () => {
    apiMocks.uploadImage.mockRejectedValue(new Error("raw upload secret-token"));
    const user = userEvent.setup();

    render(<SubmitPage />);
    await user.type(screen.getByRole("textbox", { name: /^Full Name/i }), "Ali Khan");
    await user.upload(
      screen.getByLabelText("Profile picture"),
      new File(["photo"], "family.jpg", { type: "image/jpeg" }),
    );

    expect(await screen.findByRole("alert", { name: "Photo upload failed" })).toHaveTextContent(
      "The profile photo could not be uploaded.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("secret-token");
    expect(screen.getByRole("textbox", { name: /^Full Name/i })).toHaveValue("Ali Khan");
  });

  it("keeps the form and uploaded image after a disabled submission", async () => {
    apiMocks.uploadImage.mockResolvedValue({ url: "https://images.example/profile.jpg" });
    apiMocks.submitDirectForm.mockRejectedValue(
      new ApiProblem(503, "PUBLIC_WRITES_DISABLED", "raw provider detail"),
    );
    const user = userEvent.setup();

    render(<SubmitPage />);
    await user.type(screen.getByRole("textbox", { name: /^Full Name/i }), "Ali Khan");
    await user.type(screen.getByRole("textbox", { name: /^Father's Full Name/i }), "Omar Khan");
    await user.upload(
      screen.getByLabelText("Profile picture"),
      new File(["photo"], "profile.jpg", { type: "image/jpeg" }),
    );
    await screen.findByText("Image Uploaded");
    await user.click(screen.getByRole("button", { name: "Submit to Family Archive" }));

    expect(await screen.findByRole("alert", { name: "Submission failed" })).toHaveTextContent(
      "Submissions are temporarily unavailable.",
    );
    expect(screen.getByRole("textbox", { name: /^Full Name/i })).toHaveValue("Ali Khan");
    expect(screen.getByRole("textbox", { name: /^Father's Full Name/i })).toHaveValue(
      "Omar Khan",
    );
    expect(screen.getByText("Image Uploaded")).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Submission Received" })).not.toBeInTheDocument();
  });
});

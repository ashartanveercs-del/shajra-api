import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import AsyncState from "./AsyncState";

describe("AsyncState", () => {
  it("announces loading without exposing an unavailable action", () => {
    render(
      <AsyncState
        state="loading"
        title="Loading family tree"
        actionLabel="Retry"
        onAction={vi.fn()}
      />,
    );

    expect(screen.getByRole("status")).toHaveTextContent("Loading family tree");
    expect(screen.queryByRole("button", { name: "Retry" })).not.toBeInTheDocument();
  });

  it("announces an error and runs its retry action", async () => {
    const retry = vi.fn();
    const user = userEvent.setup();

    render(
      <AsyncState
        state="error"
        title="Tree unavailable"
        message="Family records could not be loaded."
        actionLabel="Retry"
        onAction={retry}
      />,
    );

    expect(screen.getByRole("alert")).toHaveTextContent("Tree unavailable");
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Family records could not be loaded.",
    );

    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(retry).toHaveBeenCalledOnce();
  });

  it.each([
    ["empty", "No family members yet"],
    ["partial", "Some family details are unavailable"],
  ] as const)("renders the %s state with its title", (state, title) => {
    render(<AsyncState state={state} title={title} />);

    expect(screen.getByText(title)).toBeInTheDocument();
  });
});

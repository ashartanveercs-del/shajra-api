import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiProblem } from "@/lib/http";

const apiMocks = vi.hoisted(() => ({ fetchMapMarkers: vi.fn() }));

vi.mock("@/lib/api", () => apiMocks);

import MapPage from "./page";

describe("MapPage load states", () => {
  beforeEach(() => apiMocks.fetchMapMarkers.mockReset());

  it("preserves the selected filter while retrying a failed map read", async () => {
    apiMocks.fetchMapMarkers
      .mockRejectedValueOnce(new ApiProblem(503, "REQUEST_FAILED", "raw map detail"))
      .mockResolvedValueOnce({
        markers: [
          {
            id: "member-1",
            name: "Ali Khan",
            type: "residence",
            lat: 31.5,
            lng: 74.3,
          },
        ],
        arcs: [],
      });
    const user = userEvent.setup();

    render(<MapPage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Map unavailable");
    const burialFilter = screen.getByRole("button", { name: /Burial Sites/i });
    await user.click(burialFilter);
    await user.click(screen.getByRole("button", { name: "Retry" }));

    expect(await screen.findByText("No burial sites match this filter")).toBeInTheDocument();
    expect(burialFilter).toHaveAttribute("aria-pressed", "true");
    expect(apiMocks.fetchMapMarkers).toHaveBeenCalledTimes(2);
  });
});

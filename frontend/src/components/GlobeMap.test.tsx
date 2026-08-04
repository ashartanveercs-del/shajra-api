import { forwardRef, useImperativeHandle } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const globeMock = vi.hoisted(() => ({ captureProps: vi.fn() }));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("react-globe.gl", () => ({
  default: forwardRef(function GlobeMock(props: Record<string, unknown>, ref) {
    useImperativeHandle(ref, () => ({
      controls: () => ({ autoRotate: false, autoRotateSpeed: 0 }),
    }));
    globeMock.captureProps(props);
    return (
      <button type="button" onClick={() => (props.onGlobeReady as () => void)()}>
        Mark globe ready
      </button>
    );
  }),
}));

import GlobeMap from "./GlobeMap";

describe("GlobeMap rendering frame", () => {
  beforeEach(() => {
    globeMock.captureProps.mockReset();
  });

  it("keeps a stable measured frame while the renderer becomes ready", async () => {
    const user = userEvent.setup();
    render(<GlobeMap data={{ markers: [], arcs: [] }} />);

    const frame = screen.getByRole("region", { name: "Interactive heritage globe" });
    expect(frame).toHaveClass("h-full", "min-h-0", "rounded-lg", "overflow-hidden");
    expect(frame).not.toHaveStyle({ minHeight: "500px" });
    expect(screen.getByRole("status")).toHaveTextContent("Preparing interactive globe");

    await user.click(screen.getByRole("button", { name: "Mark globe ready" }));

    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    const latestProps = globeMock.captureProps.mock.lastCall?.[0] as Record<string, unknown>;
    expect(latestProps.width).toBeTypeOf("number");
    expect(latestProps.height).toBeTypeOf("number");
  });
});

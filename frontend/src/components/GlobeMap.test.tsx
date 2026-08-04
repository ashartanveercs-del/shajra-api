import { forwardRef, useImperativeHandle } from "react";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const globeMock = vi.hoisted(() => ({
  captureProps: vi.fn(),
  controls: { autoRotate: false, autoRotateSpeed: 0 },
}));

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
vi.mock("react-globe.gl", () => ({
  default: forwardRef(function GlobeMock(props: Record<string, unknown>, ref) {
    useImperativeHandle(ref, () => ({
      controls: () => globeMock.controls,
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

function latestGlobeProps() {
  return globeMock.captureProps.mock.lastCall?.[0] as Record<string, unknown>;
}

function resizeEntry(target: Element, width: number, height: number): ResizeObserverEntry {
  const contentRect: DOMRectReadOnly = {
    width,
    height,
    top: 0,
    right: width,
    bottom: height,
    left: 0,
    x: 0,
    y: 0,
    toJSON: () => ({}),
  };
  const boxSize: ResizeObserverSize = { inlineSize: width, blockSize: height };
  return {
    target,
    contentRect,
    borderBoxSize: [boxSize],
    contentBoxSize: [boxSize],
    devicePixelContentBoxSize: [boxSize],
  };
}

describe("GlobeMap rendering frame", () => {
  beforeEach(() => {
    globeMock.captureProps.mockReset();
    globeMock.controls.autoRotate = false;
    globeMock.controls.autoRotateSpeed = 0;
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it("uses ResizeObserver dimensions and disconnects before late callbacks", async () => {
    let resizeCallback!: ResizeObserverCallback;
    const observe = vi.fn();
    const disconnect = vi.fn();
    const unobserveMock = vi.fn();
    const observer: ResizeObserver = { observe, disconnect, unobserve: unobserveMock };
    class ResizeObserverMock {
      constructor(callback: ResizeObserverCallback) {
        resizeCallback = callback;
      }
      observe = observe;
      disconnect = disconnect;
      unobserve = unobserveMock;
    }
    vi.stubGlobal("ResizeObserver", ResizeObserverMock);
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({
      width: 300,
      height: 200,
      top: 0,
      right: 300,
      bottom: 200,
      left: 0,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    });
    const user = userEvent.setup();
    const { unmount } = render(<GlobeMap data={{ markers: [], arcs: [] }} />);

    const frame = screen.getByRole("region", { name: "Interactive heritage globe" });
    expect(frame).toHaveClass("h-full", "min-h-0", "rounded-lg", "overflow-hidden");
    expect(frame).not.toHaveStyle({ minHeight: "500px" });
    expect(observe).toHaveBeenCalledWith(frame);
    expect(latestGlobeProps()).toMatchObject({ width: 300, height: 200 });

    act(() => {
      resizeCallback(
        [resizeEntry(frame, 640, 360)],
        observer,
      );
    });

    expect(latestGlobeProps()).toMatchObject({ width: 640, height: 360 });
    expect(screen.getByRole("status")).toHaveTextContent("Preparing interactive globe");
    await user.click(screen.getByRole("button", { name: "Mark globe ready" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
    expect(globeMock.controls).toEqual({ autoRotate: true, autoRotateSpeed: 0.5 });

    unmount();
    expect(disconnect).toHaveBeenCalledTimes(1);
    const renderCount = globeMock.captureProps.mock.calls.length;
    expect(() => {
      resizeCallback(
        [resizeEntry(frame, 900, 500)],
        observer,
      );
    }).not.toThrow();
    expect(globeMock.captureProps).toHaveBeenCalledTimes(renderCount);
  });

  it("removes the window resize fallback and ignores its late callback", () => {
    vi.stubGlobal("ResizeObserver", undefined);
    let width = 320;
    let height = 180;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(() => ({
      width,
      height,
      top: 0,
      right: width,
      bottom: height,
      left: 0,
      x: 0,
      y: 0,
      toJSON: () => ({}),
    }));
    const addEventListener = vi.spyOn(window, "addEventListener");
    const removeEventListener = vi.spyOn(window, "removeEventListener");
    const { unmount } = render(<GlobeMap data={{ markers: [], arcs: [] }} />);

    expect(latestGlobeProps()).toMatchObject({ width: 320, height: 180 });
    const resizeListener = addEventListener.mock.calls.find(([type]) => type === "resize")?.[1];
    expect(resizeListener).toBeTypeOf("function");

    width = 480;
    height = 240;
    act(() => (resizeListener as EventListener)(new Event("resize")));
    expect(latestGlobeProps()).toMatchObject({ width: 480, height: 240 });

    unmount();
    expect(removeEventListener).toHaveBeenCalledWith("resize", resizeListener);
    const renderCount = globeMock.captureProps.mock.calls.length;
    width = 720;
    height = 400;
    expect(() => (resizeListener as EventListener)(new Event("resize"))).not.toThrow();
    expect(globeMock.captureProps).toHaveBeenCalledTimes(renderCount);
  });
});

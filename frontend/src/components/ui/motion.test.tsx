import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import AnimatedCounter from "./AnimatedCounter";
import EmberParticles from "./EmberParticles";
import Reveal from "./Reveal";

function setReducedMotion(matches: boolean) {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches,
    media: query,
    onchange: null,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    addListener: vi.fn(),
    removeListener: vi.fn(),
    dispatchEvent: vi.fn(),
  }));
}

describe("motion-safe visual enhancements", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reveals ordinary content when IntersectionObserver is unavailable", () => {
    setReducedMotion(false);
    vi.stubGlobal("IntersectionObserver", undefined);

    render(<Reveal>Family history</Reveal>);

    expect(screen.getByText("Family history")).toHaveStyle({
      opacity: "1",
      transform: "none",
    });
  });

  it("shows the final counter value when animation cannot be observed", () => {
    setReducedMotion(false);
    vi.stubGlobal("IntersectionObserver", undefined);

    render(<AnimatedCounter to={42} suffix=" members" />);

    expect(screen.getByText("42 members")).toBeInTheDocument();
  });

  it("sets reveal and counter starting states before observer-driven animation", () => {
    setReducedMotion(false);
    const observe = vi.fn();
    vi.stubGlobal(
      "IntersectionObserver",
      class IntersectionObserverMock {
        observe = observe;
        disconnect = vi.fn();
      },
    );

    render(
      <Reveal>
        Family history: <AnimatedCounter to={42} suffix=" members" />
      </Reveal>,
    );

    expect(screen.getByText(/Family history:/)).toHaveStyle({
      opacity: "0",
      transform: "translateY(22px)",
    });
    expect(screen.getByText("0 members")).toBeInTheDocument();
    expect(observe).toHaveBeenCalledTimes(2);
  });

  it("does not create a canvas when WebGL is unavailable", () => {
    setReducedMotion(false);
    vi.stubGlobal("WebGLRenderingContext", undefined);
    vi.stubGlobal("WebGL2RenderingContext", undefined);

    const { container } = render(<EmberParticles />);

    expect(container.querySelector("canvas")).not.toBeInTheDocument();
  });

  it("renders reduced-motion content without observer-driven animation", () => {
    setReducedMotion(true);
    vi.stubGlobal("IntersectionObserver", undefined);

    render(
      <Reveal>
        <AnimatedCounter to={7} suffix=" generations" />
      </Reveal>,
    );

    expect(screen.getByText("7 generations")).toBeVisible();
  });
});

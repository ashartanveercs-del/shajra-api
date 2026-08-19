import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { StrictMode } from "react";
import * as THREE from "three";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Member } from "@/lib/api";
import FamilyTree3D, * as familyTree3DModule from "./FamilyTree3D";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const TREE: Member[] = [
  {
    id: "root",
    FullName: "Root Person",
    Spouse: { id: "spouse", FullName: "Root Spouse" },
    children: [{ id: "child", FullName: "Child Person", children: [] }],
  },
];

describe("FamilyTree3D progressive enhancement", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reveals visible member links to sighted keyboard users", async () => {
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
    const user = userEvent.setup();

    render(<FamilyTree3D tree={TREE} />);

    const navigation = screen.getByRole("navigation", {
      name: "3D family tree members",
    });
    const trigger = screen.getByRole("button", { name: "Browse family members" });

    expect(navigation).not.toHaveClass("sr-only");
    expect(trigger).toHaveAttribute("aria-expanded", "false");

    await user.tab();

    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    await user.keyboard("{Enter}");
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    const rootLink = screen.getByRole("link", { name: "Root Person" });
    expect(rootLink).toHaveAttribute(
      "href",
      "/member/root",
    );
    expect(rootLink).toHaveClass("focus-visible:outline-2");
    expect(screen.getByRole("link", { name: "Root Spouse" })).toHaveAttribute(
      "href",
      "/member/spouse",
    );
    expect(screen.getByRole("link", { name: "Child Person" })).toHaveAttribute(
      "href",
      "/member/child",
    );

    await user.keyboard("{Enter}");
    expect(trigger).toHaveFocus();
    expect(trigger).toHaveAttribute("aria-expanded", "false");
  });

  it("notifies once when WebGL context creation fails in Strict Mode", async () => {
    vi.stubGlobal("matchMedia", () => ({ matches: false }));
    vi.stubGlobal("WebGLRenderingContext", class WebGLRenderingContext {});
    vi.stubGlobal("WebGL2RenderingContext", class WebGL2RenderingContext {});
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(null);
    const onUnavailable = vi.fn();

    render(
      <StrictMode>
        <FamilyTree3D tree={TREE} onUnavailable={onUnavailable} />
      </StrictMode>,
    );

    await waitFor(() => expect(onUnavailable).toHaveBeenCalledOnce());
  });

  it("includes spouses in the flattened graph without duplicating records", () => {
    const flattenTree = (
      familyTree3DModule as unknown as {
        flattenTreeFor3D?: (tree: Member[]) => Array<{ member: Member; parentId: string | null }>;
      }
    ).flattenTreeFor3D;

    expect(flattenTree).toBeTypeOf("function");
    const flattened = flattenTree?.(TREE) ?? [];
    expect(flattened.map((node) => node.member.id)).toEqual(["root", "spouse", "child"]);
    expect(flattened.find((node) => node.member.id === "spouse")?.parentId).toBe("root");
  });

  it("prefers a canonical ancestry node over an earlier spouse snapshot", () => {
    const tree: Member[] = [
      {
        id: "root-a",
        FullName: "Root A",
        Spouse: {
          id: "root-b",
          FullName: "Stale spouse snapshot",
          Gender: "Female",
          _isSpouseRef: true,
        },
        children: [],
      },
      {
        id: "root-b",
        FullName: "Canonical Root B",
        Gender: "Female",
        children: [{ id: "child-b", FullName: "Child B", children: [] }],
      },
    ];

    const flattened = familyTree3DModule.flattenTreeFor3D(tree);
    const rootB = flattened.find((node) => node.member.id === "root-b");

    expect(flattened.filter((node) => node.member.id === "root-b")).toHaveLength(1);
    expect(rootB).toMatchObject({
      member: { FullName: "Canonical Root B" },
      parentId: null,
      relationship: "root",
    });
    expect(rootB?.member).not.toHaveProperty("_isSpouseRef");
    expect(flattened.map((node) => node.member.id)).toContain("child-b");
  });

  it("preserves a spouse edge when both people use canonical ancestry nodes", () => {
    const tree: Member[] = [
      {
        id: "root-a",
        FullName: "Root A",
        Spouse: { id: "root-b", FullName: "Root B", _isSpouseRef: true },
        children: [],
      },
      {
        id: "root-b",
        FullName: "Root B",
        Spouse: { id: "root-a", FullName: "Root A", _isSpouseRef: true },
        children: [],
      },
    ];

    expect(familyTree3DModule.collectSpouseLinksFor3D(tree)).toEqual([
      { sourceId: "root-a", targetId: "root-b" },
    ]);
  });

  it("renders synthetic placeholders without linking to missing member pages", async () => {
    vi.stubGlobal("matchMedia", () => ({ matches: true }));
    const user = userEvent.setup();
    const tree: Member[] = [
      {
        id: "root",
        FullName: "Root Person",
        children: [
          {
            id: "__name__root__father",
            FullName: "Name-only Relative",
            Gender: "Male",
            IsPlaceholder: true,
            children: [],
          },
        ],
      },
    ];

    render(<FamilyTree3D tree={tree} />);
    await user.click(screen.getByRole("button", { name: "Browse family members" }));

    expect(screen.getByText("Name-only Relative")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Name-only Relative" })).not.toBeInTheDocument();
  });

  it("spaces dense generations according to label size", () => {
    const flattenTree = (
      familyTree3DModule as unknown as {
        flattenTreeFor3D?: (tree: Member[]) => Array<unknown>;
      }
    ).flattenTreeFor3D;
    const buildLayout = (
      familyTree3DModule as unknown as {
        buildThreeLayout?: (
          nodes: Array<unknown>,
          viewportWidth: number,
        ) => Array<{ depth: number; x: number; z: number; labelWidth: number }>;
      }
    ).buildThreeLayout;
    const denseTree: Member[] = [
      {
        id: "dense-root",
        FullName: "Dense Root",
        children: Array.from({ length: 12 }, (_, index) => ({
          id: `child-${index}`,
          FullName: `Child ${index}`,
          children: [],
        })),
      },
    ];

    expect(flattenTree).toBeTypeOf("function");
    expect(buildLayout).toBeTypeOf("function");
    const layout = buildLayout?.(flattenTree?.(denseTree) ?? [], 390) ?? [];
    const generation = layout.filter((node) => node.depth === 1);
    const minimumDistance = Math.min(
      ...generation.flatMap((node, index) =>
        generation.slice(index + 1).map((other) => Math.hypot(node.x - other.x, node.z - other.z)),
      ),
    );

    expect(generation).toHaveLength(12);
    expect(minimumDistance).toBeGreaterThanOrEqual(generation[0].labelWidth + 1.5);
  });

  it("disposes sprite textures as well as geometry and materials", () => {
    const disposeScene = (
      familyTree3DModule as unknown as {
        disposeThreeSceneResources?: (scene: THREE.Scene) => void;
      }
    ).disposeThreeSceneResources;
    const texture = new THREE.Texture();
    const material = new THREE.SpriteMaterial({ map: texture });
    const sprite = new THREE.Sprite(material);
    const scene = new THREE.Scene();
    scene.add(sprite);
    const textureDispose = vi.spyOn(texture, "dispose");
    const materialDispose = vi.spyOn(material, "dispose");

    expect(disposeScene).toBeTypeOf("function");
    disposeScene?.(scene);

    expect(textureDispose).toHaveBeenCalledOnce();
    expect(materialDispose).toHaveBeenCalledOnce();
  });
});

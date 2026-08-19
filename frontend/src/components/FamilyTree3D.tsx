"use client";

import Link from "next/link";
import { ChevronUp, User } from "lucide-react";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";

import type { Member } from "@/lib/api";

export type FlatTreeNode = {
  member: Member;
  depth: number;
  parentId: string | null;
  relationship: "root" | "child" | "spouse";
};

export type ThreeLayoutNode = FlatTreeNode & {
  x: number;
  y: number;
  z: number;
  labelWidth: number;
};

export type ThreeSpouseLink = {
  sourceId: string;
  targetId: string;
};

const BASE_RADIUS = 3.5;
const LEVEL_HEIGHT = 8;
const BG = 0xf3ede4;
const TEXT_DARK = "#2c2418";
const BRONZE = 0x8b6f47;
const WARM_GOLD = 0xc4956a;
const PLUM = 0x7a5680;
const CREATOR_GOLD = 0xd99a2b;

const GENDER_COLORS: Record<string, number> = {
  Male: BRONZE,
  Female: PLUM,
};

function isNavigableMember(member: Member): boolean {
  return Boolean(
    member.id &&
    !member.IsPlaceholder &&
    !member.id.startsWith("__name__"),
  );
}

export function flattenTreeFor3D(nodes: Member[]): FlatTreeNode[] {
  const flattened: FlatTreeNode[] = [];
  const included = new Set<string>();
  const expanded = new Set<string>();
  const structuralIds = new Set<string>();

  const collectStructuralIds = (list: Member[]) => {
    for (const member of list) {
      if (member.id) structuralIds.add(member.id);
      collectStructuralIds(member.children ?? []);
    }
  };
  collectStructuralIds(nodes);

  const include = (node: FlatTreeNode) => {
    if (!node.member.id || included.has(node.member.id)) return;
    included.add(node.member.id);
    flattened.push(node);
  };

  const walk = (
    list: Member[],
    depth: number,
    parentId: string | null,
  ) => {
    for (const member of list) {
      include({
        member,
        depth,
        parentId,
        relationship: parentId ? "child" : "root",
      });

      if (member.Spouse && !structuralIds.has(member.Spouse.id)) {
        include({
          member: member.Spouse,
          depth,
          parentId: member.id,
          relationship: "spouse",
        });
      }

      if (!member.id || expanded.has(member.id)) continue;
      expanded.add(member.id);
      if (member.children?.length) {
        walk(member.children, depth + 1, member.id);
      }
    }
  };

  walk(nodes, 0, null);
  return flattened;
}

function relationshipKey(firstId: string, secondId: string): string {
  return [firstId, secondId].sort().join("\u0000");
}

export function collectSpouseLinksFor3D(nodes: Member[]): ThreeSpouseLink[] {
  const links: ThreeSpouseLink[] = [];
  const included = new Set<string>();

  const walk = (members: Member[]) => {
    for (const member of members) {
      const spouseId = member.Spouse?.id;
      if (member.id && spouseId && member.id !== spouseId) {
        const key = relationshipKey(member.id, spouseId);
        if (!included.has(key)) {
          included.add(key);
          links.push({ sourceId: member.id, targetId: spouseId });
        }
      }
      walk(member.children ?? []);
    }
  };

  walk(nodes);
  return links;
}

export function buildThreeLayout(
  nodes: FlatTreeNode[],
  viewportWidth: number,
): ThreeLayoutNode[] {
  const labelWidth = Math.max(7, Math.min(11, viewportWidth / 90));
  const byDepth = new Map<number, FlatTreeNode[]>();

  for (const node of nodes) {
    const generation = byDepth.get(node.depth) ?? [];
    generation.push(node);
    byDepth.set(node.depth, generation);
  }

  return [...byDepth.entries()].flatMap(([depth, generation]) => {
    const count = generation.length;
    const desiredSpacing = labelWidth + 1.75;
    const spacingRadius =
      count > 1 ? desiredSpacing / (2 * Math.sin(Math.PI / count)) : 0;
    const radius = Math.max(BASE_RADIUS + depth * 4.5, spacingRadius);
    const offset = depth * 0.7;

    return generation.map((node, index) => {
      const angle = (index / count) * Math.PI * 2 + offset;
      return {
        ...node,
        x: Math.cos(angle) * radius,
        y: depth * LEVEL_HEIGHT,
        z: Math.sin(angle) * radius,
        labelWidth,
      };
    });
  });
}

export function disposeThreeSceneResources(scene: THREE.Scene) {
  const geometries = new Set<THREE.BufferGeometry>();
  const materials = new Set<THREE.Material>();
  const textures = new Set<THREE.Texture>();

  scene.traverse((object) => {
    const renderable = object as THREE.Object3D & {
      geometry?: THREE.BufferGeometry;
      material?: THREE.Material | THREE.Material[];
    };
    if (renderable.geometry) geometries.add(renderable.geometry);

    const objectMaterials = Array.isArray(renderable.material)
      ? renderable.material
      : renderable.material
        ? [renderable.material]
        : [];
    for (const material of objectMaterials) {
      materials.add(material);
      for (const value of Object.values(material)) {
        if (value instanceof THREE.Texture) textures.add(value);
      }
    }
  });

  textures.forEach((texture) => texture.dispose());
  materials.forEach((material) => material.dispose());
  geometries.forEach((geometry) => geometry.dispose());
}

function makeLabelSprite(
  name: string,
  isCreator: boolean,
  labelWidth: number,
): THREE.Sprite | null {
  const canvas = document.createElement("canvas");
  const scale = 2;
  const width = 384;
  const height = 120;
  canvas.width = width * scale;
  canvas.height = height * scale;
  const context = canvas.getContext("2d");
  if (!context) return null;
  context.scale(scale, scale);

  const radius = 22;
  context.beginPath();
  context.moveTo(radius, 0);
  context.arcTo(width, 0, width, height, radius);
  context.arcTo(width, height, 0, height, radius);
  context.arcTo(0, height, 0, 0, radius);
  context.arcTo(0, 0, width, 0, radius);
  context.closePath();
  context.fillStyle = isCreator ? "#f59e0b" : "#ffffff";
  context.fill();
  context.strokeStyle = isCreator ? "#7c4a03" : "#a68b5b";
  context.lineWidth = 4;
  context.stroke();

  const shortName = name.split(" ").slice(0, 2).join(" ");
  context.fillStyle = TEXT_DARK;
  context.font = "700 38px Inter, system-ui, sans-serif";
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.fillText(shortName, width / 2, height / 2 - (isCreator ? 8 : 0));
  if (isCreator) {
    context.font = "700 19px Inter, system-ui, sans-serif";
    context.fillText("CREATOR", width / 2, height / 2 + 30);
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  texture.anisotropy = 4;
  const material = new THREE.SpriteMaterial({
    map: texture,
    depthWrite: false,
    transparent: true,
  });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(labelWidth, labelWidth * 0.3125, 1);
  sprite.renderOrder = 10;
  return sprite;
}

function createRenderer(): THREE.WebGLRenderer | null {
  if (
    typeof window.WebGLRenderingContext !== "function" &&
    typeof window.WebGL2RenderingContext !== "function"
  ) {
    return null;
  }

  try {
    const canvas = document.createElement("canvas");
    const context =
      canvas.getContext("webgl2", { alpha: true, antialias: true }) ??
      canvas.getContext("webgl", { alpha: true, antialias: true });
    if (!context) return null;
    return new THREE.WebGLRenderer({
      alpha: true,
      antialias: true,
      canvas,
      context,
    });
  } catch {
    return null;
  }
}

export default function FamilyTree3D({
  tree,
  onUnavailable,
}: {
  tree: Member[];
  onUnavailable?: () => void;
}) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const unavailableNotifiedRef = useRef(false);
  const navigationId = useId();
  const navigationButtonRef = useRef<HTMLButtonElement | null>(null);
  const [navigationOpen, setNavigationOpen] = useState(false);
  const router = useRouter();
  const flatTree = useMemo(() => flattenTreeFor3D(tree), [tree]);
  const spouseLinks = useMemo(() => collectSpouseLinksFor3D(tree), [tree]);
  const notifyUnavailable = useCallback(() => {
    if (!onUnavailable || unavailableNotifiedRef.current) return;
    unavailableNotifiedRef.current = true;
    onUnavailable();
  }, [onUnavailable]);

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || flatTree.length === 0) return;
    const reducedMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducedMotion) {
      notifyUnavailable();
      return;
    }

    const renderer = createRenderer();
    if (!renderer) {
      notifyUnavailable();
      return;
    }

    const width = mount.clientWidth || 1;
    const height = mount.clientHeight || 1;
    const layout = buildThreeLayout(flatTree, width);
    const maxRadius = Math.max(
      BASE_RADIUS,
      ...layout.map((node) => Math.hypot(node.x, node.z)),
    );
    const maxDepth = Math.max(0, ...layout.map((node) => node.depth));

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(BG);
    scene.fog = new THREE.Fog(BG, Math.max(50, maxRadius * 2), Math.max(150, maxRadius * 7));

    const camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 500);
    camera.position.set(
      0,
      Math.max(30, maxDepth * LEVEL_HEIGHT + 18),
      Math.max(32, maxRadius * 2.4),
    );

    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(width, height);
    renderer.domElement.setAttribute("aria-hidden", "true");
    renderer.domElement.tabIndex = -1;
    mount.appendChild(renderer.domElement);

    scene.add(new THREE.AmbientLight(0xfff6e8, 1.1));
    const keyLight = new THREE.DirectionalLight(0xfff1de, 1.5);
    keyLight.position.set(20, 32, 22);
    scene.add(keyLight);
    const rimLight = new THREE.DirectionalLight(WARM_GOLD, 0.5);
    rimLight.position.set(-18, 6, -16);
    scene.add(rimLight);

    const dustGeometry = new THREE.BufferGeometry();
    const dustPositions = new Float32Array(360 * 3);
    for (let index = 0; index < 360; index += 1) {
      const vector = new THREE.Vector3()
        .randomDirection()
        .multiplyScalar(maxRadius * 1.5 + Math.random() * maxRadius * 2);
      dustPositions[index * 3] = vector.x;
      dustPositions[index * 3 + 1] = vector.y;
      dustPositions[index * 3 + 2] = vector.z;
    }
    dustGeometry.setAttribute(
      "position",
      new THREE.BufferAttribute(dustPositions, 3),
    );
    const dust = new THREE.Points(
      dustGeometry,
      new THREE.PointsMaterial({
        color: WARM_GOLD,
        opacity: 0.3,
        size: 0.2,
        sizeAttenuation: true,
        transparent: true,
      }),
    );
    scene.add(dust);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.5;
    controls.minDistance = 10;
    controls.maxDistance = Math.max(120, maxRadius * 6);
    controls.target.set(0, (maxDepth * LEVEL_HEIGHT) / 2, 0);
    controls.maxPolarAngle = Math.PI * 0.58;

    const positions = new Map<string, THREE.Vector3>();
    const navigableSpheres: THREE.Mesh[] = [];
    const sphereToId = new Map<THREE.Mesh, string>();

    for (const node of layout) {
      const position = new THREE.Vector3(node.x, node.y, node.z);
      positions.set(node.member.id, position);
      const isCreator = (node.member.FullName ?? "").trim() === "Ashar Tanveer";
      const color = isCreator
        ? CREATOR_GOLD
        : GENDER_COLORS[node.member.Gender ?? ""] ?? BRONZE;
      const material = new THREE.MeshStandardMaterial({
        color,
        emissive: color,
        emissiveIntensity: isCreator ? 0.55 : 0.25,
        metalness: 0.1,
        roughness: 0.4,
      });
      const sphere = new THREE.Mesh(
        new THREE.SphereGeometry(0.9, 24, 24),
        material,
      );
      sphere.position.copy(position);
      sphere.userData.isCreator = isCreator;
      scene.add(sphere);
      if (isNavigableMember(node.member)) {
        navigableSpheres.push(sphere);
        sphereToId.set(sphere, node.member.id);
      }

      const label = makeLabelSprite(
        node.member.FullName ?? "Unknown",
        isCreator,
        node.labelWidth,
      );
      if (label) {
        label.position.copy(position).add(new THREE.Vector3(0, 2.2, 0));
        scene.add(label);
      }
    }

    const connections = new THREE.Group();
    scene.add(connections);
    const renderedSpouseLinks = new Set<string>();
    const addConnection = (
      start: THREE.Vector3,
      end: THREE.Vector3,
      relationship: "child" | "spouse",
    ) => {
      const middle = new THREE.Vector3().addVectors(start, end).multiplyScalar(0.5);
      middle.y += relationship === "spouse" ? 0 : 1.4;
      const curve = new THREE.QuadraticBezierCurve3(start, middle, end);
      connections.add(
        new THREE.Mesh(
          new THREE.TubeGeometry(
            curve,
            32,
            relationship === "spouse" ? 0.22 : 0.14,
            8,
            false,
          ),
          new THREE.MeshBasicMaterial({
            color: relationship === "spouse" ? PLUM : WARM_GOLD,
            opacity: 0.72,
            transparent: true,
          }),
        ),
      );
    };
    for (const node of layout) {
      if (!node.parentId) continue;
      const end = positions.get(node.member.id);
      const start = positions.get(node.parentId);
      if (!start || !end) continue;
      const relationship = node.relationship === "spouse" ? "spouse" : "child";
      addConnection(start, end, relationship);
      if (relationship === "spouse") {
        renderedSpouseLinks.add(relationshipKey(node.parentId, node.member.id));
      }
    }
    for (const link of spouseLinks) {
      const key = relationshipKey(link.sourceId, link.targetId);
      if (renderedSpouseLinks.has(key)) continue;
      const start = positions.get(link.sourceId);
      const end = positions.get(link.targetId);
      if (!start || !end) continue;
      addConnection(start, end, "spouse");
    }

    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2(2, 2);
    let hovered: THREE.Mesh | null = null;

    const updatePointer = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      if (!rect.width || !rect.height) return;
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    };

    const handlePointerDown = (event: PointerEvent) => {
      updatePointer(event);
      raycaster.setFromCamera(pointer, camera);
      const [hit] = raycaster.intersectObjects(navigableSpheres);
      const id = hit ? sphereToId.get(hit.object as THREE.Mesh) : undefined;
      if (id) router.push(`/member/${id}`);
    };

    renderer.domElement.addEventListener("pointermove", updatePointer);
    renderer.domElement.addEventListener("pointerdown", handlePointerDown);

    const timer = new THREE.Timer();
    timer.connect(document);
    let animationFrame = 0;
    const animate = (timestamp: number) => {
      animationFrame = requestAnimationFrame(animate);
      timer.update(timestamp);
      const elapsed = timer.getElapsed();
      raycaster.setFromCamera(pointer, camera);
      const [hit] = raycaster.intersectObjects(navigableSpheres);
      const next = hit ? (hit.object as THREE.Mesh) : null;
      if (next !== hovered) {
        if (hovered) {
          (hovered.material as THREE.MeshStandardMaterial).emissiveIntensity =
            hovered.userData.isCreator ? 0.55 : 0.25;
        }
        hovered = next;
        if (hovered) {
          (hovered.material as THREE.MeshStandardMaterial).emissiveIntensity = 1;
        }
        mount.style.cursor = hovered ? "pointer" : "grab";
      }
      dust.rotation.y = elapsed * 0.008;
      controls.update();
      renderer.render(scene, camera);
    };
    animationFrame = requestAnimationFrame(animate);

    const handleResize = () => {
      const nextWidth = mount.clientWidth || 1;
      const nextHeight = mount.clientHeight || 1;
      camera.aspect = nextWidth / nextHeight;
      camera.updateProjectionMatrix();
      renderer.setSize(nextWidth, nextHeight);
    };
    window.addEventListener("resize", handleResize);

    return () => {
      cancelAnimationFrame(animationFrame);
      timer.dispose();
      window.removeEventListener("resize", handleResize);
      renderer.domElement.removeEventListener("pointermove", updatePointer);
      renderer.domElement.removeEventListener("pointerdown", handlePointerDown);
      controls.dispose();
      disposeThreeSceneResources(scene);
      renderer.renderLists.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      if (renderer.domElement.parentNode === mount) {
        mount.removeChild(renderer.domElement);
      }
    };
  }, [flatTree, notifyUnavailable, router, spouseLinks]);

  return (
    <div className="relative h-full w-full">
      <div ref={mountRef} className="h-full w-full" aria-hidden="true" />
      <nav
        aria-label="3D family tree members"
        className={`absolute bottom-3 left-3 z-10 flex max-w-[calc(100%-1.5rem)] flex-col-reverse ${navigationOpen ? "w-72" : "w-auto"}`}
        onBlurCapture={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
            setNavigationOpen(false);
          }
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape" && navigationOpen) {
            event.preventDefault();
            setNavigationOpen(false);
            navigationButtonRef.current?.focus();
          }
        }}
      >
        <button
          ref={navigationButtonRef}
          type="button"
          aria-controls={navigationId}
          aria-expanded={navigationOpen}
          aria-label="Browse family members"
          onClick={() => setNavigationOpen((current) => !current)}
          className="flex h-10 w-full items-center gap-2 rounded-md border border-border bg-bg-card/95 px-3 text-sm font-semibold text-text-primary shadow-sm backdrop-blur-sm transition-colors hover:border-accent/50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          <User aria-hidden="true" className="h-4 w-4 shrink-0 text-accent" />
          <span className="truncate">Family members</span>
          <ChevronUp
            aria-hidden="true"
            className={`ml-auto h-4 w-4 shrink-0 text-text-muted transition-transform ${navigationOpen ? "rotate-180" : ""}`}
          />
        </button>
        <ul
          id={navigationId}
          hidden={!navigationOpen}
          className="mb-1 max-h-52 overflow-y-auto rounded-md border border-border bg-bg-card/95 p-1 shadow-lg backdrop-blur-sm"
        >
          {flatTree.map(({ member }) => {
            const label = member.FullName || "Unknown family member";
            return (
              <li key={member.id}>
                {isNavigableMember(member) ? (
                  <Link
                    href={`/member/${member.id}`}
                    className="block truncate rounded px-3 py-2 text-sm text-text-primary transition-colors hover:bg-bg-secondary focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent"
                  >
                    {label}
                  </Link>
                ) : (
                  <span className="block truncate px-3 py-2 text-sm text-text-muted">
                    {label}
                  </span>
                )}
              </li>
            );
          })}
        </ul>
      </nav>
    </div>
  );
}

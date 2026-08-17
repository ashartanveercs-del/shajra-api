"use client";

import { useEffect, useRef } from "react";
import { useRouter } from "next/navigation";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import type { Member } from "@/lib/api";

/**
 * 3D orbital family tree — warm parchment heritage theme. Generations radiate
 * in concentric rings, connected by thick bronze arcs. Large, high-contrast
 * name labels for readability. Drag to orbit, scroll to zoom, click a node.
 */

type FlatNode = {
  member: Member;
  depth: number;
  parentId: string | null;
};

const BASE_RADIUS = 3.5;

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

function flattenTree(nodes: Member[]): FlatNode[] {
  const out: FlatNode[] = [];
  const walk = (list: Member[], depth: number, parentId: string | null) => {
    for (const node of list) {
      out.push({ member: node, depth, parentId });
      if (node.children?.length) walk(node.children, depth + 1, node.id);
    }
  };
  walk(nodes, 0, null);
  return out;
}

function makeLabelSprite(name: string, isCreator: boolean): THREE.Sprite {
  // High-resolution canvas so text stays crisp when scaled up.
  const canvas = document.createElement("canvas");
  const s = 3;
  canvas.width = 480 * s;
  canvas.height = 150 * s;
  const ctx = canvas.getContext("2d")!;
  ctx.scale(s, s);

  const w = 480;
  const h = 150;
  const radius = 28;
  ctx.beginPath();
  ctx.moveTo(radius, 0);
  ctx.arcTo(w, 0, w, h, radius);
  ctx.arcTo(w, h, 0, h, radius);
  ctx.arcTo(0, h, 0, 0, radius);
  ctx.arcTo(0, 0, w, 0, radius);
  ctx.closePath();

  if (isCreator) {
    ctx.fillStyle = "#f59e0b";
    ctx.fill();
    ctx.strokeStyle = "#7c4a03";
    ctx.lineWidth = 5;
    ctx.stroke();
  } else {
    ctx.fillStyle = "#ffffff";
    ctx.fill();
    ctx.strokeStyle = "#a68b5b";
    ctx.lineWidth = 4;
    ctx.stroke();
  }

  const short = name.split(" ").slice(0, 2).join(" ");
  ctx.fillStyle = isCreator ? "#2c2418" : TEXT_DARK;
  ctx.font = "700 52px Inter, system-ui, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText(short, w / 2, h / 2 - (isCreator ? 10 : 0));
  if (isCreator) {
    ctx.font = "700 26px Inter, sans-serif";
    ctx.fillStyle = "#2c2418";
    ctx.fillText("★ CREATOR", w / 2, h / 2 + 40);
  }

  const texture = new THREE.CanvasTexture(canvas);
  texture.minFilter = THREE.LinearFilter;
  texture.anisotropy = 4;
  const material = new THREE.SpriteMaterial({ map: texture, depthWrite: false, transparent: true });
  const sprite = new THREE.Sprite(material);
  sprite.scale.set(13, 4.05, 1);
  sprite.renderOrder = 10;
  return sprite;
}

export default function FamilyTree3D({ tree }: { tree: Member[] }) {
  const mountRef = useRef<HTMLDivElement | null>(null);
  const router = useRouter();

  useEffect(() => {
    const mount = mountRef.current;
    if (!mount || tree.length === 0) return;
    if (typeof window.matchMedia === "function" && window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const width = mount.clientWidth || 1;
    const height = mount.clientHeight || 1;

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(BG);
    scene.fog = new THREE.Fog(BG, 50, 150);

    const camera = new THREE.PerspectiveCamera(50, width / height, 0.1, 400);
    camera.position.set(0, 38, 26);

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setSize(width, height);
    mount.appendChild(renderer.domElement);

    scene.add(new THREE.AmbientLight(0xfff6e8, 1.1));
    const key = new THREE.DirectionalLight(0xfff1de, 1.5);
    key.position.set(20, 32, 22);
    scene.add(key);
    const rim = new THREE.DirectionalLight(0xc4956a, 0.5);
    rim.position.set(-18, 6, -16);
    scene.add(rim);

    // Soft warm dust motes
    const dustGeo = new THREE.BufferGeometry();
    const dustCount = 500;
    const dustPos = new Float32Array(dustCount * 3);
    for (let i = 0; i < dustCount; i++) {
      const v = new THREE.Vector3().randomDirection().multiplyScalar(50 + Math.random() * 45);
      dustPos[i * 3] = v.x;
      dustPos[i * 3 + 1] = v.y;
      dustPos[i * 3 + 2] = v.z;
    }
    dustGeo.setAttribute("position", new THREE.BufferAttribute(dustPos, 3));
    const dust = new THREE.Points(
      dustGeo,
      new THREE.PointsMaterial({ color: WARM_GOLD, size: 0.2, transparent: true, opacity: 0.3, sizeAttenuation: true }),
    );
    scene.add(dust);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = true;
    controls.dampingFactor = 0.08;
    controls.autoRotate = true;
    controls.autoRotateSpeed = 0.5;
    controls.minDistance = 10;
    controls.maxDistance = 120;
    controls.target.set(0, 6, 0);
    controls.maxPolarAngle = Math.PI * 0.52;

    const flat = flattenTree(tree);
    const byDepth = new Map<number, FlatNode[]>();
    for (const node of flat) {
      const arr = byDepth.get(node.depth) ?? [];
      arr.push(node);
      byDepth.set(node.depth, arr);
    }

    // Vertical generation levels (clear "wedding-cake" structure) so labels
    // never stack on top of each other.
    const LEVEL_HEIGHT = 8;
    const positions = new Map<string, THREE.Vector3>();
    const spheres: THREE.Mesh[] = [];
    const sphereToId = new Map<THREE.Mesh, string>();

    for (const [depthStr, nodes] of byDepth) {
      const depth = Number(depthStr);
      const radius = BASE_RADIUS + depth * 1.6;
      const count = nodes.length;
      const offset = depth * 0.7;
      nodes.forEach((node, i) => {
        const angle = (i / count) * Math.PI * 2 + offset;
        const y = depth * LEVEL_HEIGHT;
        const pos = new THREE.Vector3(Math.cos(angle) * radius, y, Math.sin(angle) * radius);
        positions.set(node.member.id, pos);

        const isCreator = (node.member.FullName ?? "").trim().includes("Ashar Tanveer");
        const color = isCreator ? CREATOR_GOLD : GENDER_COLORS[node.member.Gender ?? ""] ?? BRONZE;

        const sphere = new THREE.Mesh(
          new THREE.SphereGeometry(1.0, 32, 32),
          new THREE.MeshStandardMaterial({
            color,
            roughness: 0.4,
            metalness: 0.1,
            emissive: color,
            emissiveIntensity: isCreator ? 0.55 : 0.25,
          }),
        );
        sphere.position.copy(pos);
        sphere.userData.isCreator = isCreator;
        scene.add(sphere);
        spheres.push(sphere);
        sphereToId.set(sphere, node.member.id);

        const sprite = makeLabelSprite(node.member.FullName ?? "?", isCreator);
        sprite.position.copy(pos).add(new THREE.Vector3(0, 2.4, 0));
        scene.add(sprite);
      });
    }

    // Thick bronze connection tubes (parent → child)
    const connectionGroup = new THREE.Group();
    scene.add(connectionGroup);
    for (const node of flat) {
      if (!node.parentId) continue;
      const childPos = positions.get(node.member.id);
      const parentPos = positions.get(node.parentId);
      if (!childPos || !parentPos) continue;

      const mid = new THREE.Vector3().addVectors(childPos, parentPos).multiplyScalar(0.5);
      mid.y += 1.4; // gentle upward arc between levels

      const curve = new THREE.QuadraticBezierCurve3(childPos, mid, parentPos);
      const tube = new THREE.Mesh(
        new THREE.TubeGeometry(curve, 40, 0.16, 8, false),
        new THREE.MeshBasicMaterial({ color: WARM_GOLD, transparent: true, opacity: 0.7 }),
      );
      connectionGroup.add(tube);
    }

    // Hover / click
    const raycaster = new THREE.Raycaster();
    const pointer = new THREE.Vector2();
    let hovered: THREE.Mesh | null = null;

    const updatePointer = (event: PointerEvent) => {
      const rect = renderer.domElement.getBoundingClientRect();
      pointer.x = ((event.clientX - rect.left) / rect.width) * 2 - 1;
      pointer.y = -((event.clientY - rect.top) / rect.height) * 2 + 1;
    };

    const onPointerDown = (event: PointerEvent) => {
      updatePointer(event);
      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(spheres);
      if (hits.length > 0) {
        const id = sphereToId.get(hits[0].object as THREE.Mesh);
        if (id) router.push(`/member/${id}`);
      }
    };

    renderer.domElement.addEventListener("pointermove", updatePointer);
    renderer.domElement.addEventListener("pointerdown", onPointerDown);

    let raf = 0;
    const clock = new THREE.Clock();
    const animate = () => {
      raf = requestAnimationFrame(animate);
      const t = clock.getElapsedTime();

      raycaster.setFromCamera(pointer, camera);
      const hits = raycaster.intersectObjects(spheres);
      const next = hits.length > 0 ? (hits[0].object as THREE.Mesh) : null;
      if (next !== hovered) {
        if (hovered) (hovered.material as THREE.MeshStandardMaterial).emissiveIntensity = hovered.userData.isCreator ? 0.55 : 0.25;
        hovered = next;
        if (hovered) (hovered.material as THREE.MeshStandardMaterial).emissiveIntensity = 1.0;
        mount.style.cursor = hovered ? "pointer" : "grab";
      }

      dust.rotation.y = t * 0.008;
      controls.update();
      renderer.render(scene, camera);
    };
    animate();

    const onResize = () => {
      const w = mount.clientWidth || 1;
      const h = mount.clientHeight || 1;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };
    window.addEventListener("resize", onResize);

    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", onResize);
      renderer.domElement.removeEventListener("pointermove", updatePointer);
      renderer.domElement.removeEventListener("pointerdown", onPointerDown);
      controls.dispose();
      scene.traverse((obj) => {
        if (obj instanceof THREE.Mesh || obj instanceof THREE.Line || obj instanceof THREE.Points || obj instanceof THREE.Sprite) {
          obj.geometry?.dispose?.();
          const mat = obj.material as THREE.Material | THREE.Material[];
          if (Array.isArray(mat)) mat.forEach((m) => m.dispose());
          else mat?.dispose?.();
        }
      });
      renderer.dispose();
      if (renderer.domElement.parentNode === mount) mount.removeChild(renderer.domElement);
    };
  }, [tree, router]);

  return <div ref={mountRef} className="h-full w-full" />;
}

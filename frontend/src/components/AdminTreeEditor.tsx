"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent as ReactKeyboardEvent,
} from "react";
import {
  Edit3,
  Heart,
  Link2Off,
  Loader2,
  Save,
  Trash2,
  UserPlus,
  X,
} from "lucide-react";

import AsyncState from "@/components/feedback/AsyncState";
import {
  adminCreateMember,
  adminDeleteMember,
  adminUpdateMember,
  fetchTree,
  uploadImage,
  type Member,
} from "@/lib/api";
import { asApiProblem, type Loadable } from "@/lib/loadable";

type CardStyle = {
  badge?: string;
  badgeColor?: string;
  bg?: string;
  borderColor?: string;
  glow?: string;
  className?: string;
};

function parseCardStyle(member: Member): CardStyle | null {
  if (!member.CardStyle) return null;
  try {
    const parsed: unknown = JSON.parse(member.CardStyle);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
    const badge = Reflect.get(parsed, "badge");
    const badgeColor = Reflect.get(parsed, "badgeColor");
    const bg = Reflect.get(parsed, "bg");
    const borderColor = Reflect.get(parsed, "borderColor");
    const glow = Reflect.get(parsed, "glow");
    const className = Reflect.get(parsed, "className");
    const style: CardStyle = {
      ...(typeof badge === "string" ? { badge } : {}),
      ...(typeof badgeColor === "string" ? { badgeColor } : {}),
      ...(typeof bg === "string" ? { bg } : {}),
      ...(typeof borderColor === "string" ? { borderColor } : {}),
      ...(typeof glow === "string" ? { glow } : {}),
      ...(typeof className === "string" ? { className } : {}),
    };
    return Object.keys(style).length > 0 ? style : null;
  } catch {
    return null;
  }
}

type ParentRelationship = Pick<Partial<Member>, "FatherRecordId" | "MotherRecordId">;

function legacyParentRelationship(parent: Member): ParentRelationship {
  if (parent.Spouse) {
    return parent.Gender === "Male"
      ? { FatherRecordId: parent.id, MotherRecordId: parent.Spouse.id }
      : { MotherRecordId: parent.id, FatherRecordId: parent.Spouse.id };
  }
  return parent.Gender === "Female"
    ? { MotherRecordId: parent.id }
    : { FatherRecordId: parent.id };
}

function flattenTree(nodes: Member[]): Member[] {
  const members = nodes.flatMap((node) => [
    node,
    ...(node.Spouse ? [node.Spouse] : []),
    ...flattenTree(node.children ?? []),
  ]);
  return Array.from(new Map(members.map((member) => [member.id, member])).values());
}

function genderClasses(gender?: string) {
  if (gender === "Male") return "bg-sky-light/80 border-sky/30 text-sky-900";
  if (gender === "Female") return "bg-plum-light/80 border-plum/30 text-plum-900";
  return "bg-bg-secondary border-border text-text-primary";
}

function PersonCard({
  member,
  style,
  onSelect,
}: {
  member: Member;
  style: CardStyle | null;
  onSelect: (member: Member, invoker: HTMLElement) => void;
}) {
  const isCreator = (member.FullName ?? "").trim().includes("Ashar Tanveer");
  const badge = style?.badge || (isCreator ? "CREATOR" : null);
  const isPlaceholder = member.IsPlaceholder || (member.FullName ?? "").includes("(Unknown)");

  return (
    <button
      type="button"
      onClick={(event) => {
        event.stopPropagation();
        onSelect(member, event.currentTarget);
      }}
      className="relative flex min-w-[90px] flex-col items-center"
      aria-label={`Edit ${member.FullName || "member"}`}
    >
      {badge && (
        <span
          className="absolute -top-3 left-1/2 z-[110] -translate-x-1/2 whitespace-nowrap rounded-full px-2.5 py-0.5 text-[9px] font-bold tracking-widest text-white shadow-lg"
          style={{ background: style?.badgeColor || "var(--accent)" }}
        >
          {badge}
        </span>
      )}
      <span
        className={`relative z-10 mb-1 flex h-10 w-10 items-center justify-center rounded-full border bg-white/70 bg-cover bg-center font-serif text-base font-bold shadow-sm sm:h-12 sm:w-12 sm:text-lg ${isPlaceholder ? "border-dashed border-text-light/40 opacity-60" : "border-black/5"}`}
        style={member.ProfileImageUrl ? { backgroundImage: `url(${member.ProfileImageUrl})` } : undefined}
      >
        {!member.ProfileImageUrl && (member.FullName || "?")[0]}
        {member.IsAlive && (
          <span className="absolute -right-1 -top-1 z-20 flex h-3.5 w-3.5 items-center justify-center rounded-full border border-emerald/20 bg-white shadow-sm">
            <Heart aria-hidden="true" className="h-2 w-2 fill-emerald text-emerald" />
          </span>
        )}
      </span>
      <span className="line-clamp-2 max-w-[100px] text-center text-[11px] font-semibold leading-tight opacity-90 sm:text-xs">
        {member.FullName?.split(" ").slice(0, 2).join(" ")}
      </span>
      {member.CurrentCity && (
        <span className="mt-0.5 max-w-[90px] truncate text-[8px] opacity-40">
          {member.CurrentCity}
        </span>
      )}
    </button>
  );
}

type TreeActions = {
  onNodeClick: (member: Member, invoker: HTMLElement) => void;
  onDropNode: (draggedId: string, targetId: string) => void;
  onDelete: (id: string) => void;
  onUnlink: (id: string) => void;
  onAddChild: (member: Member, invoker: HTMLElement) => void;
};

function AdminTreeCard({
  member,
  onNodeClick,
  onDropNode,
  onDelete,
  onUnlink,
  onAddChild,
}: TreeActions & { member: Member }) {
  const [isOver, setIsOver] = useState(false);
  const cardStyle = parseCardStyle(member);
  const spouseCardStyle = member.Spouse ? parseCardStyle(member.Spouse) : null;
  const customStyle: CSSProperties = {
    ...(cardStyle?.bg ? { background: cardStyle.bg } : {}),
    ...(cardStyle?.borderColor ? { borderColor: cardStyle.borderColor } : {}),
    ...(cardStyle?.glow ? { boxShadow: cardStyle.glow } : {}),
  };
  const isCreator = [member, member.Spouse].some((person) =>
    (person?.FullName ?? "").trim().includes("Ashar Tanveer"),
  );
  const isPlaceholder = member.IsPlaceholder || (member.FullName ?? "").includes("(Unknown)");

  const handleDragStart = (event: DragEvent<HTMLDivElement>) => {
    event.dataTransfer.setData("text/plain", member.id);
    event.dataTransfer.effectAllowed = "move";
  };

  const handleDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setIsOver(false);
    const draggedId = event.dataTransfer.getData("text/plain");
    if (draggedId && draggedId !== member.id) onDropNode(draggedId, member.id);
  };

  return (
    <div className="group inline-block">
      <div
        draggable
        onDragStart={handleDragStart}
        onDragOver={(event) => {
          event.preventDefault();
          event.stopPropagation();
          setIsOver(true);
          event.dataTransfer.dropEffect = "move";
        }}
        onDragLeave={() => setIsOver(false)}
        onDrop={handleDrop}
        className={`relative flex min-w-[120px] cursor-grab flex-col items-center rounded-lg border p-3 shadow-sm ring-2 transition-all active:cursor-grabbing ${genderClasses(member.Gender)} ${isOver ? "scale-105 border-accent ring-accent shadow-lg" : "ring-transparent"} ${isCreator ? "border-accent ring-accent/30 shadow-[0_0_15px_rgba(231,166,26,0.3)]" : ""} ${isPlaceholder ? "border-dashed opacity-70" : ""} ${cardStyle?.className || ""}`}
        style={customStyle}
      >
        <div className="absolute left-1 top-1 z-[100] flex items-center gap-1 rounded-lg border border-sky/20 bg-white/95 p-1 text-text-primary opacity-0 shadow-xl backdrop-blur transition-all group-hover:opacity-100 group-focus-within:opacity-100">
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onAddChild(member, event.currentTarget);
            }}
            className="rounded p-1 text-emerald hover:bg-emerald/10"
            aria-label={`Add child to ${member.FullName || "member"}`}
          >
            <UserPlus aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onDelete(member.id);
            }}
            className="rounded p-1 text-terracotta hover:bg-terracotta/10"
            aria-label={`Delete ${member.FullName || "member"}`}
          >
            <Trash2 aria-hidden="true" className="h-3.5 w-3.5" />
          </button>
        </div>

        {member.Spouse && (
          <div className="absolute right-1 top-1 z-[100] flex items-center gap-1 rounded-lg border border-plum/20 bg-white/95 p-1 text-text-primary opacity-0 shadow-xl backdrop-blur transition-all group-hover:opacity-100 group-focus-within:opacity-100">
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onNodeClick(member.Spouse as Member, event.currentTarget);
              }}
              className="rounded p-1 text-plum-700 hover:bg-plum/10"
              aria-label={`Edit ${member.Spouse.FullName || "spouse"}`}
            >
              <Edit3 aria-hidden="true" className="h-3.5 w-3.5" />
            </button>
            <button
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onUnlink(member.id);
              }}
              className="rounded p-1 text-text-muted hover:bg-black/5"
              aria-label={`Unlink ${member.FullName || "member"} and ${member.Spouse.FullName || "spouse"}`}
            >
              <Link2Off aria-hidden="true" className="h-3.5 w-3.5" />
            </button>
          </div>
        )}

        <div className="mb-1 flex items-start gap-3">
          <PersonCard member={member} style={cardStyle} onSelect={onNodeClick} />
          {member.Spouse && (
            <>
              <div className="flex h-10 items-center justify-center sm:h-12">
                <div className="relative h-px w-4 bg-border/50">
                  <Heart aria-hidden="true" className="absolute -top-1 left-1/2 h-2.5 w-2.5 -translate-x-1/2 fill-plum/20 text-plum" />
                </div>
              </div>
              <PersonCard member={member.Spouse} style={spouseCardStyle} onSelect={onNodeClick} />
            </>
          )}
        </div>
        {member.Spouse && (
          <div className="mt-1 text-[10px] font-bold uppercase opacity-40">Family Unit</div>
        )}
      </div>
    </div>
  );
}

function AdminFamilyTreeNode({ member, ...actions }: TreeActions & { member: Member }) {
  const linePos = member.Spouse ? "calc(50% - 64px)" : "50%";
  return (
    <li style={{ "--line-pos": linePos } as CSSProperties}>
      <AdminTreeCard member={member} {...actions} />
      {member.children && member.children.length > 0 && (
        <ul>
          {member.children.map((child) => (
            <AdminFamilyTreeNode key={child.id} member={child} {...actions} />
          ))}
        </ul>
      )}
    </li>
  );
}

function memberDraft(member: Member): Partial<Member> {
  return {
    FullName: member.FullName,
    FatherName: member.FatherName,
    MotherName: member.MotherName,
    SpouseName: member.SpouseName,
    SpouseRecordId: member.SpouseRecordId,
    DateOfBirth: member.DateOfBirth,
    DateOfDeath: member.DateOfDeath,
    Gender: member.Gender,
    IsAlive: member.IsAlive ?? true,
    ProfileImageUrl: member.ProfileImageUrl,
    CurrentCity: member.CurrentCity,
    CurrentCountry: member.CurrentCountry,
    Email: member.Email,
    PhoneNumber: member.PhoneNumber,
    Biography: member.Biography,
    FatherRecordId: member.FatherRecordId,
    MotherRecordId: member.MotherRecordId,
  };
}

export default function AdminTreeEditor({ token, onUpdated }: { token: string; onUpdated?: () => void }) {
  const [treeState, setTreeState] = useState<Loadable<Member[]>>({ status: "loading" });
  const [actionLoading, setActionLoading] = useState(false);
  const [actionError, setActionError] = useState("");
  const [editingMember, setEditingMember] = useState<Member | null>(null);
  const [editForm, setEditForm] = useState<Partial<Member>>({});
  const [uploadingImage, setUploadingImage] = useState(false);
  const treeRequest = useRef(0);
  const modalGeneration = useRef(0);
  const editingMemberRef = useRef<Member | null>(null);
  const modalInvokerRef = useRef<HTMLElement | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeButtonRef = useRef<HTMLButtonElement>(null);
  const tree = useMemo(() => ("data" in treeState ? treeState.data : []), [treeState]);
  const allMembers = useMemo(() => flattenTree(tree), [tree]);

  const modalIsCurrent = useCallback((generation: number, memberId: string) => (
    modalGeneration.current === generation && editingMemberRef.current?.id === memberId
  ), []);

  const loadData = useCallback(async (showLoading = false) => {
    const requestId = ++treeRequest.current;
    if (showLoading) setTreeState({ status: "loading" });
    try {
      const data = await fetchTree();
      if (requestId !== treeRequest.current) return;
      setTreeState(data.length > 0 ? { status: "ready", data } : { status: "empty", data });
    } catch (error: unknown) {
      if (requestId !== treeRequest.current) return;
      setTreeState({
        status: "error",
        problem: asApiProblem(error, "The editor tree could not be loaded."),
      });
    }
  }, []);

  useEffect(() => {
    void loadData();
    return () => {
      treeRequest.current += 1;
    };
  }, [loadData]);

  useEffect(() => {
    if (editingMember) closeButtonRef.current?.focus();
  }, [editingMember]);

  const refreshAfterWrite = () => {
    void loadData();
    onUpdated?.();
  };

  const handlePhotoUpload = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    const member = editingMemberRef.current;
    if (!file || !member) return;
    const generation = modalGeneration.current;
    const memberId = member.id;
    setUploadingImage(true);
    setActionError("");
    try {
      const data = await uploadImage(file);
      if (!modalIsCurrent(generation, memberId)) return;
      setEditForm((current) => ({ ...current, ProfileImageUrl: data.url }));
    } catch (error: unknown) {
      if (modalIsCurrent(generation, memberId)) {
        setActionError(asApiProblem(error, "The image could not be uploaded.").message);
      }
    } finally {
      if (modalIsCurrent(generation, memberId)) setUploadingImage(false);
    }
  };

  const handleDropNode = async (draggedId: string, targetId: string) => {
    const target = allMembers.find((member) => member.id === targetId);
    const updates = target ? legacyParentRelationship(target) : {};

    setActionLoading(true);
    setActionError("");
    try {
      await adminUpdateMember(token, draggedId, updates);
      refreshAfterWrite();
    } catch (error: unknown) {
      setActionError(asApiProblem(error, "The family connection could not be updated.").message);
    } finally {
      setActionLoading(false);
    }
  };

  const handleSaveEdit = async () => {
    const member = editingMemberRef.current;
    if (!member) return;
    const generation = modalGeneration.current;
    const memberId = member.id;
    setActionLoading(true);
    setActionError("");
    try {
      await adminUpdateMember(token, memberId, editForm);
      // Make the marriage reciprocal in the underlying data (the tree renders
      // it correctly either way, but keeping both sides consistent prevents
      // stale one-sided links from confusing later edits).
      const newSpouseId = editForm.SpouseRecordId;
      const oldSpouseId = member.SpouseRecordId;
      if (newSpouseId && newSpouseId !== oldSpouseId) {
        await adminUpdateMember(token, newSpouseId, { SpouseRecordId: memberId });
      }
      if (!newSpouseId && oldSpouseId && !oldSpouseId.startsWith("__name__")) {
        await adminUpdateMember(token, oldSpouseId, { SpouseRecordId: "", SpouseName: "" });
      }
      if (!modalIsCurrent(generation, memberId)) return;
      closeEditor();
      refreshAfterWrite();
    } catch (error: unknown) {
      if (modalIsCurrent(generation, memberId)) {
        setActionError(asApiProblem(error, "The member changes could not be saved.").message);
      }
    } finally {
      if (modalIsCurrent(generation, memberId)) setActionLoading(false);
    }
  };

  const handleUnlinkMember = async (id: string) => {
    const target = allMembers.find((member) => member.id === id);
    const spouseId = target?.Spouse?.id;
    setActionLoading(true);
    setActionError("");
    try {
      // Unlink the marriage: clear this member's spouse link, and clear the
      // reciprocal link on the spouse (when it's a real record — name-only
      // placeholder nodes have no record to update).
      await adminUpdateMember(token, id, { SpouseRecordId: "", SpouseName: "" });
      if (spouseId && !spouseId.startsWith("__name__")) {
        await adminUpdateMember(token, spouseId, { SpouseRecordId: "", SpouseName: "" });
      }
      refreshAfterWrite();
    } catch (error: unknown) {
      setActionError(asApiProblem(error, "The marriage could not be removed.").message);
    } finally {
      setActionLoading(false);
    }
  };

  const handleDeleteMember = async (id: string) => {
    const target = allMembers.find((member) => member.id === id);
    if (!window.confirm(`Delete "${target?.FullName || id}"? This cannot be undone easily.`)) return;
    setActionLoading(true);
    setActionError("");
    try {
      await adminDeleteMember(token, id);
      refreshAfterWrite();
    } catch (error: unknown) {
      setActionError(asApiProblem(error, "The member could not be deleted.").message);
    } finally {
      setActionLoading(false);
    }
  };

  const openEditor = (member: Member, draft: Partial<Member>, invoker: HTMLElement) => {
    modalGeneration.current += 1;
    editingMemberRef.current = member;
    modalInvokerRef.current = invoker;
    setActionLoading(false);
    setUploadingImage(false);
    setActionError("");
    setEditingMember(member);
    setEditForm(draft);
  };

  const openNewMember = (parent: Member | undefined, invoker: HTMLElement) => {
    const draft: Partial<Member> = {
      FullName: "",
      Gender: "Male",
      IsAlive: true,
      ProfileImageUrl: "",
      ...(parent ? { Generation: (parent.Generation || 1) + 1 } : {}),
      ...(parent ? legacyParentRelationship(parent) : {}),
      ...(parent
        ? parent.Gender === "Female"
          ? { MotherName: parent.FullName, FatherName: parent.Spouse?.FullName || "" }
          : { FatherName: parent.FullName, MotherName: parent.Spouse?.FullName || "" }
        : {}),
    };
    openEditor({ id: "new", FullName: "" }, draft, invoker);
  };

  const handleCreateNew = async () => {
    const member = editingMemberRef.current;
    if (!member) return;
    const generation = modalGeneration.current;
    const memberId = member.id;
    setActionLoading(true);
    setActionError("");
    try {
      await adminCreateMember(token, editForm);
      if (!modalIsCurrent(generation, memberId)) return;
      closeEditor();
      refreshAfterWrite();
    } catch (error: unknown) {
      if (modalIsCurrent(generation, memberId)) {
        setActionError(asApiProblem(error, "The member could not be created.").message);
      }
    } finally {
      if (modalIsCurrent(generation, memberId)) setActionLoading(false);
    }
  };

  const closeEditor = () => {
    const invoker = modalInvokerRef.current;
    modalGeneration.current += 1;
    editingMemberRef.current = null;
    setEditingMember(null);
    setActionLoading(false);
    setUploadingImage(false);
    setActionError("");
    queueMicrotask(() => {
      if (invoker?.isConnected) invoker.focus();
    });
  };

  const handleDialogKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key === "Escape") {
      event.preventDefault();
      event.stopPropagation();
      closeEditor();
      return;
    }
    if (event.key !== "Tab") return;

    const dialog = dialogRef.current;
    if (!dialog) return;
    const focusable = Array.from(dialog.querySelectorAll<HTMLElement>(
      "button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
    )).filter((element) => !element.hasAttribute("hidden"));
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    const active = document.activeElement;
    if (event.shiftKey && (active === first || !dialog.contains(active))) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && active === last) {
      event.preventDefault();
      first.focus();
    }
  };

  return (
    <div className="relative">
      <style dangerouslySetInnerHTML={{
        __html: `
        .family-tree { display: block; padding-bottom: 3rem; }
        .family-tree > ul { padding-top: 0; display: flex; justify-content: center; width: max-content; margin: 0 auto; }
        .family-tree ul { padding-top: 30px; position: relative; display: flex; justify-content: center; }
        .family-tree li { float: left; text-align: center; list-style-type: none; position: relative; padding: 30px 10px 0; }
        .family-tree li::before, .family-tree li::after { content: ''; position: absolute; top: 0; height: 30px; }
        .family-tree li::before { right: calc(100% - var(--line-pos, 50%)); width: var(--line-pos, 50%); border-top: 2px solid #cbd5e1; }
        .family-tree li::after { left: var(--line-pos, 50%); width: calc(100% - var(--line-pos, 50%)); border-left: 2px solid #cbd5e1; border-top: 2px solid #cbd5e1; }
        .family-tree li:only-child::after, .family-tree li:only-child::before { display: none; }
        .family-tree li:only-child { padding-top: 0; }
        .family-tree li:first-child::before, .family-tree li:last-child::after { border: 0 none; }
        .family-tree li:last-child::before { border-right: 2px solid #cbd5e1; border-radius: 0 5px 0 0; }
        .family-tree li:first-child::after { border-radius: 5px 0 0 0; }
        .family-tree ul ul::before { content: ''; position: absolute; top: 0; left: 50%; border-left: 2px solid #cbd5e1; width: 0; height: 30px; transform: translateX(-1px); }
      ` }} />

      <div
        aria-hidden={editingMember ? true : undefined}
        inert={editingMember ? true : undefined}
        className="relative flex h-[65vh] min-h-0 supports-[height:100dvh]:h-[65dvh] flex-col overflow-hidden rounded-lg border border-border bg-[#f8f6f0] shadow-inner"
      >
        <div className="z-10 flex min-h-16 shrink-0 items-center justify-between gap-3 border-b border-border bg-white/80 px-4 py-3 backdrop-blur sm:px-5">
          <h3 className="flex min-w-0 items-center gap-2 font-serif text-base font-bold sm:text-lg">
            <span className="truncate">Interactive Heritage Tree</span>
            {actionLoading && <Loader2 aria-label="Saving tree changes" className="h-4 w-4 shrink-0 animate-spin text-accent" />}
          </h3>
          <button
            type="button"
            onClick={(event) => openNewMember(undefined, event.currentTarget)}
            aria-label="Add New Member"
            className="btn-primary min-h-10 shrink-0 px-3 text-sm sm:px-4"
          >
            <UserPlus aria-hidden="true" className="h-4 w-4" />
            <span className="hidden sm:inline">Add New Member</span>
            <span className="sm:hidden">Add</span>
          </button>
        </div>

        {actionError && !editingMember && (
          <div role="alert" className="shrink-0 border-b border-terracotta/30 bg-terracotta/10 px-4 py-2 text-sm text-terracotta">
            {actionError}
          </div>
        )}

        <div className="relative min-h-0 flex-1 overflow-auto custom-scrollbar">
          {treeState.status === "loading" && (
            <AsyncState state="loading" title="Loading editor tree" />
          )}
          {treeState.status === "error" && (
            <AsyncState
              state="error"
              title="Editor tree unavailable"
              message={treeState.problem.message}
              actionLabel="Retry"
              onAction={() => void loadData(true)}
            />
          )}
          {treeState.status === "empty" && (
            <AsyncState state="empty" title="No family members yet" message="Add a member to begin the family tree." />
          )}
          {treeState.status === "ready" && (
            <div className="family-tree min-w-max p-12 pb-20">
              <ul>
                {treeState.data.map((root) => (
                  <AdminFamilyTreeNode
                    key={root.id}
                    member={root}
                    onDelete={(id) => void handleDeleteMember(id)}
                    onUnlink={(id) => void handleUnlinkMember(id)}
                    onAddChild={openNewMember}
                    onNodeClick={(member, invoker) => openEditor(member, memberDraft(member), invoker)}
                    onDropNode={(draggedId, targetId) => void handleDropNode(draggedId, targetId)}
                  />
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      {editingMember && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 p-4 backdrop-blur-sm">
          <div
            ref={dialogRef}
            role="dialog"
            aria-modal="true"
            aria-labelledby="member-editor-title"
            onKeyDown={handleDialogKeyDown}
            className="flex max-h-[90vh] w-full max-w-2xl flex-col overflow-hidden rounded-lg bg-white shadow-xl"
          >
            <div className="flex shrink-0 items-center justify-between border-b border-border bg-bg-secondary p-4">
              <h3 id="member-editor-title" className="font-serif text-lg font-bold text-text-primary">
                Edit Member Info
              </h3>
              <button ref={closeButtonRef} type="button" onClick={closeEditor} className="p-1 text-text-muted hover:text-text-primary" aria-label="Close member editor">
                <X aria-hidden="true" className="h-5 w-5" />
              </button>
            </div>

            <div className="space-y-5 overflow-y-auto bg-bg-primary p-6 custom-scrollbar">
              {actionError && (
                <div role="alert" className="rounded-lg border border-terracotta/30 bg-terracotta/10 px-4 py-3 text-sm text-terracotta">
                  {actionError}
                </div>
              )}

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="rounded-lg border border-sky/20 bg-sky/5 p-4">
                  <label htmlFor="editor-father" className="mb-1.5 block text-xs font-bold uppercase text-sky">Structural Father</label>
                  <select id="editor-father" value={editForm.FatherRecordId || ""} onChange={(event) => setEditForm((current) => ({ ...current, FatherRecordId: event.target.value }))} className="input-heritage relative z-50 w-full bg-white">
                    <option value="">No father</option>
                    {allMembers.filter((member) => member.id !== editingMember.id).map((member) => (
                      <option key={member.id} value={member.id}>{member.FullName}</option>
                    ))}
                  </select>
                </div>
                <div className="rounded-lg border border-plum/20 bg-plum/5 p-4">
                  <label htmlFor="editor-mother" className="mb-1.5 block text-xs font-bold uppercase text-plum">Structural Mother</label>
                  <select id="editor-mother" value={editForm.MotherRecordId || ""} onChange={(event) => setEditForm((current) => ({ ...current, MotherRecordId: event.target.value }))} className="input-heritage relative z-50 w-full bg-white">
                    <option value="">No mother</option>
                    {allMembers.filter((member) => member.id !== editingMember.id).map((member) => (
                      <option key={member.id} value={member.id}>{member.FullName}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="rounded-lg border border-amber/25 bg-amber/5 p-4">
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <label htmlFor="editor-spouse" className="text-xs font-bold uppercase text-amber">
                    Spouse (link to existing member)
                    <select id="editor-spouse" value={editForm.SpouseRecordId || ""} onChange={(event) => setEditForm((current) => ({ ...current, SpouseRecordId: event.target.value }))} className="input-heritage mt-1.5 w-full bg-white">
                      <option value="">No spouse</option>
                      {allMembers.filter((member) => member.id !== editingMember.id && !member.id.startsWith("__name__")).map((member) => (
                        <option key={member.id} value={member.id}>{member.FullName}</option>
                      ))}
                    </select>
                  </label>
                  <EditorInput id="editor-spouse-name" label="Spouse Name (if not in tree)" value={editForm.SpouseName} onChange={(SpouseName) => setEditForm((current) => ({ ...current, SpouseName }))} />
                </div>
                <p className="mt-2 text-[11px] leading-relaxed text-text-muted">Pick an existing member to link a marriage, or type a name for a spouse not yet in the tree.</p>
              </div>

              <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
                <EditorInput id="editor-name" label="Full Name" value={editForm.FullName} onChange={(FullName) => setEditForm((current) => ({ ...current, FullName }))} />
                <label className="block text-xs uppercase text-text-light" htmlFor="editor-gender">
                  Gender
                  <select id="editor-gender" value={editForm.Gender || "Male"} onChange={(event) => setEditForm((current) => ({ ...current, Gender: event.target.value }))} className="input-heritage mt-1.5 w-full">
                    <option value="Male">Male</option>
                    <option value="Female">Female</option>
                  </select>
                </label>
                <EditorInput id="editor-father-name" label="Father Name" value={editForm.FatherName} onChange={(FatherName) => setEditForm((current) => ({ ...current, FatherName }))} />
                <EditorInput id="editor-mother-name" label="Mother Name" value={editForm.MotherName} onChange={(MotherName) => setEditForm((current) => ({ ...current, MotherName }))} />
                <EditorInput id="editor-birth" label="Date of Birth" value={editForm.DateOfBirth} onChange={(DateOfBirth) => setEditForm((current) => ({ ...current, DateOfBirth }))} />
                <EditorInput id="editor-death" label="Date of Death" value={editForm.DateOfDeath} disabled={editForm.IsAlive} onChange={(DateOfDeath) => setEditForm((current) => ({ ...current, DateOfDeath }))} />
              </div>

              <label htmlFor="editor-alive" className="flex items-center gap-2 rounded-lg border border-border bg-bg-secondary p-3 text-sm font-medium">
                <input id="editor-alive" type="checkbox" checked={editForm.IsAlive || false} onChange={(event) => setEditForm((current) => ({ ...current, IsAlive: event.target.checked }))} className="h-5 w-5 rounded border-gray-300 text-accent focus:ring-accent" />
                This person is currently alive
              </label>

              <div className="grid grid-cols-1 gap-5 sm:grid-cols-2">
                <EditorInput id="editor-email" label="Email" type="email" value={editForm.Email} onChange={(Email) => setEditForm((current) => ({ ...current, Email }))} />
                <EditorInput id="editor-phone" label="Phone Number" type="tel" value={editForm.PhoneNumber} onChange={(PhoneNumber) => setEditForm((current) => ({ ...current, PhoneNumber }))} />
                <div className="sm:col-span-2">
                  <label htmlFor="editor-image" className="mb-1.5 block text-xs uppercase text-text-light">Profile Image</label>
                  {editForm.ProfileImageUrl ? (
                    <div className="flex items-center gap-4 rounded-lg border border-border bg-bg-primary p-3">
                      <div role="img" aria-label="Profile preview" className="h-12 w-12 shrink-0 rounded-full bg-cover bg-center" style={{ backgroundImage: `url(${editForm.ProfileImageUrl})` }} />
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-text-primary">Image Uploaded</p>
                        <button type="button" onClick={() => setEditForm((current) => ({ ...current, ProfileImageUrl: "" }))} className="text-xs text-terracotta hover:underline">Remove</button>
                      </div>
                    </div>
                  ) : (
                    <div className="relative rounded-lg border border-border bg-bg-primary">
                      <input id="editor-image" type="file" accept="image/*" onChange={(event) => void handlePhotoUpload(event)} disabled={uploadingImage} className="absolute inset-0 h-full w-full cursor-pointer opacity-0 disabled:cursor-not-allowed" />
                      <div className={`flex w-full items-center justify-center gap-2 px-4 py-2.5 ${uploadingImage ? "opacity-50" : ""}`}>
                        {uploadingImage ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin text-accent" /> : <Edit3 aria-hidden="true" className="h-4 w-4 text-text-muted" />}
                        <span className="text-sm font-medium text-text-muted">{uploadingImage ? "Uploading..." : "Select a photo"}</span>
                      </div>
                    </div>
                  )}
                </div>
                <label htmlFor="editor-biography" className="text-xs uppercase text-text-light sm:col-span-2">
                  Biography
                  <textarea id="editor-biography" rows={4} value={editForm.Biography || ""} onChange={(event) => setEditForm((current) => ({ ...current, Biography: event.target.value }))} className="input-heritage mt-1.5 w-full resize-y" />
                </label>
              </div>
            </div>

            <div className="flex shrink-0 justify-end gap-3 border-t border-border bg-bg-secondary p-4">
              <button type="button" onClick={closeEditor} className="rounded-lg px-5 py-2.5 text-sm font-semibold text-text-muted hover:text-text-primary">Cancel</button>
              <button type="button" onClick={editingMember.id === "new" ? () => void handleCreateNew() : () => void handleSaveEdit()} disabled={actionLoading} className="btn-primary">
                {actionLoading ? <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" /> : <Save aria-hidden="true" className="h-4 w-4" />}
                {editingMember.id === "new" ? "Create Member" : "Save Changes"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function EditorInput({
  id,
  label,
  value,
  onChange,
  type = "text",
  disabled = false,
}: {
  id: string;
  label: string;
  value?: string;
  onChange: (value: string) => void;
  type?: "text" | "email" | "tel";
  disabled?: boolean;
}) {
  return (
    <label htmlFor={id} className="text-xs uppercase text-text-light">
      {label}
      <input id={id} type={type} value={value || ""} onChange={(event) => onChange(event.target.value)} disabled={disabled} className="input-heritage mt-1.5 w-full disabled:opacity-50" />
    </label>
  );
}

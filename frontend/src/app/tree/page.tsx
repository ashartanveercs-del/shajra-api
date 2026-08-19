"use client";

import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { fetchTree, submitDirectForm, uploadImage, type Member } from "@/lib/api";
import AsyncState from "@/components/feedback/AsyncState";
import { asApiProblem, type Loadable } from "@/lib/loadable";
import { User, Heart, Loader2, Plus, ZoomIn, ZoomOut, Maximize, Edit3, X, Save, Crown } from "lucide-react";
import { TransformWrapper, TransformComponent } from "react-zoom-pan-pinch";
import TiltCard from "@/components/ui/TiltCard";

const FamilyTree3D = lazy(() => import("@/components/FamilyTree3D"));

type SuggestionForm = {
  fullName: string;
  fatherName: string;
  motherName: string;
  spouseName: string;
  dateOfBirth: string;
  dateOfDeath: string;
  location: string;
  biography: string;
  gender: string;
  profileImage: string;
};

const EMPTY_SUGGESTION: SuggestionForm = {
  fullName: "",
  fatherName: "",
  motherName: "",
  spouseName: "",
  dateOfBirth: "",
  dateOfDeath: "",
  location: "",
  biography: "",
  gender: "Male",
  profileImage: "",
};

function AvatarCircle({ member }: { member: Member }) {
  return (
    <div className="flex flex-col items-center">
      <div
        className="relative z-10 mb-1.5 flex h-11 w-11 items-center justify-center rounded-full border-2 border-white bg-gradient-to-br from-white to-bg-secondary bg-cover bg-center font-serif text-base font-bold text-accent shadow-md sm:h-14 sm:w-14 sm:text-xl"
        style={
          member.ProfileImageUrl
            ? { backgroundImage: `url(${member.ProfileImageUrl})` }
            : undefined
        }
      >
        {!member.ProfileImageUrl && (member.FullName || "?")[0]}
        {member.IsAlive && (
          <div className="absolute -right-0.5 -top-0.5 z-20 flex h-4 w-4 items-center justify-center rounded-full border-2 border-white bg-emerald shadow-sm">
            <Heart className="h-2 w-2 fill-white text-white" />
          </div>
        )}
      </div>
      <div className="max-w-[96px] truncate text-center text-[11px] font-semibold leading-tight text-text-primary sm:text-xs">
        {member.FullName?.split(" ").slice(0, 2).join(" ")}
      </div>
    </div>
  );
}

function TreeCard({ member, onSuggestEdit }: { member: Member & { Spouse?: Member }, onSuggestEdit: (m: Member, trigger: HTMLButtonElement) => void }) {
  const isCouple = !!member.Spouse;

  const getGenderBg = (gender?: string) => {
    if (gender === "Male") return "bg-gradient-to-br from-sky-light to-white border-sky/30 text-sky-900";
    if (gender === "Female") return "bg-gradient-to-br from-plum-light to-white border-plum/30 text-plum-900";
    return "bg-gradient-to-br from-bg-secondary to-white border-border text-text-primary";
  };

  return (
    <div className="group inline-block">
      <TiltCard maxTilt={9} className="rounded-2xl">
      <div
        className={`flex flex-col items-center p-3 rounded-2xl border ${member.FullName === "Ashar Tanveer" ? 'border-amber-400 bg-gradient-to-br from-amber-50 via-white to-amber-100 text-amber-900 shadow-[0_0_28px_rgba(231,166,26,0.5)] ring-2 ring-amber-400/50 creator-glow' : getGenderBg(member.Gender) + " shadow-sm hover:shadow-md"} transition-all min-w-[120px] relative hover:-translate-y-1 ${member.FullName?.includes("(Unknown)") ? 'border-dashed opacity-70 grayscale-[0.3]' : ''}`}
      >
        {/* Creator Badge */}
        {member.FullName === "Ashar Tanveer" && (
          <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-gradient-to-r from-amber-500 via-yellow-400 to-amber-500 text-white px-3 py-0.5 rounded-full text-[10px] font-bold tracking-widest shadow-lg shadow-amber-500/40 border border-amber-200/70 flex items-center gap-1 animate-pulse z-[110]">
            <Crown className="w-3 h-3 fill-amber-200" />
            CREATOR
          </div>
        )}
        <button 
          type="button"
          onClick={(e) => {
            e.preventDefault();
            onSuggestEdit(member, e.currentTarget);
          }}
          className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-all flex items-center gap-1.5 p-1.5 bg-white/90 backdrop-blur rounded-lg text-text-primary shadow-lg border border-accent/20 z-30 hover:bg-white"
          title="Suggest an improvement"
        >
          <Edit3 className="w-3.5 h-3.5" />
          <span className="text-[9px] font-bold uppercase tracking-tighter">Suggest Edit</span>
        </button>

        <div className="flex items-start gap-4 mb-2">
          <AvatarCircle member={member} />
          {member.Spouse && (
            <>
              <div className="h-10 sm:h-12 flex items-center justify-center">
                <div className="w-4 h-px bg-border/50 relative">
                  <Heart className="w-2.5 h-2.5 text-plum absolute -top-1 left-1/2 -translate-x-1/2 fill-plum/20" />
                </div>
              </div>
              {/* Creator Tag Check for Spouse */}
              <div className="relative">
                {member.Spouse.FullName === "Ashar Tanveer" && (
                   <div className="absolute -top-6 left-1/2 -translate-x-1/2 bg-accent text-white px-2 py-0.5 rounded-full text-[8px] font-bold shadow-lg z-[110]">CREATOR</div>
                )}
                <AvatarCircle member={member.Spouse} />
              </div>
            </>
          )}
        </div>

        {isCouple ? (
          <div className="mt-1 inline-flex items-center gap-2 text-[9px] uppercase tracking-[0.22em] font-semibold text-accent">
            <span className="w-4 h-px bg-accent/40" />
            Married
            <span className="w-4 h-px bg-accent/40" />
          </div>
        ) : (
          (member.DateOfBirth || member.DateOfDeath) && (
            <div className="text-[9px] opacity-70 mt-0.5 whitespace-nowrap">
              {member.DateOfBirth || "?"} &mdash; {member.DateOfDeath || "Present"}
            </div>
          )
        )}
      </div>
      </TiltCard>
    </div>
  );
}

function FamilyTreeNode({ member, onSuggestEdit }: { member: Member, onSuggestEdit: (m: Member, trigger: HTMLButtonElement) => void }) {
  const isCouple = !!member.Spouse;
  const linePos = isCouple ? 'calc(50% - 64px)' : '50%';

  return (
    <li style={{ '--line-pos': linePos } as React.CSSProperties}>
      <TreeCard member={member} onSuggestEdit={onSuggestEdit} />
      {member.children && member.children.length > 0 && (
        <ul>
          {member.children.map((child) => (
            <FamilyTreeNode key={child.id} member={child} onSuggestEdit={onSuggestEdit} />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function TreePage() {
  const [treeState, setTreeState] = useState<Loadable<Member[]>>({ status: "loading" });
  const treeRequest = useRef(0);
  const suggestionTrigger = useRef<HTMLButtonElement | null>(null);
  const dialogSession = useRef(0);
  const dialogMemberId = useRef<string | null>(null);
  const successTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [editingMember, setEditingMember] = useState<Member | null>(null);
  const [suggestForm, setSuggestForm] = useState<SuggestionForm>(EMPTY_SUGGESTION);
  const [submitting, setSubmitting] = useState(false);
  const [uploadingImage, setUploadingImage] = useState(false);
  const [selectedPhotoFile, setSelectedPhotoFile] = useState<File | null>(null);
  const [showSuccess, setShowSuccess] = useState(false);
  const [suggestionProblem, setSuggestionProblem] = useState<string | null>(null);
  const [uploadProblem, setUploadProblem] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"3d" | "2d">("2d");
  const [enhancementNotice, setEnhancementNotice] = useState<string | null>(null);

  const loadTree = useCallback(() => {
    const request = ++treeRequest.current;
    fetchTree().then(
      (data) => {
        if (request !== treeRequest.current) return;
        setTreeState(data.length > 0 ? { status: "ready", data } : { status: "empty", data });
      },
      (error: unknown) => {
        if (request !== treeRequest.current) return;
        setTreeState({
          status: "error",
          problem: asApiProblem(error, "Family records could not be loaded."),
        });
      },
    );
  }, []);

  useEffect(() => {
    loadTree();
    return () => {
      treeRequest.current += 1;
    };
  }, [loadTree]);

  useEffect(
    () => () => {
      dialogSession.current += 1;
      dialogMemberId.current = null;
      if (successTimer.current !== null) {
        clearTimeout(successTimer.current);
      }
    },
    [],
  );

  const retryTree = () => {
    setTreeState({ status: "loading" });
    loadTree();
  };

  const tree = "data" in treeState ? treeState.data : [];

  const clearSuccessTimer = () => {
    if (successTimer.current !== null) {
      clearTimeout(successTimer.current);
      successTimer.current = null;
    }
  };

  const isActiveDialog = (session: number, memberId: string) =>
    dialogSession.current === session && dialogMemberId.current === memberId;

  const uploadSuggestionPhoto = async (file: File) => {
    if (!editingMember) return;
    const session = dialogSession.current;
    const memberId = editingMember.id;
    setUploadingImage(true);
    setUploadProblem(null);
    try {
      const data = await uploadImage(file);
      if (!isActiveDialog(session, memberId)) return;
      setSuggestForm((current) => ({ ...current, profileImage: data.url }));
    } catch (error: unknown) {
      if (!isActiveDialog(session, memberId)) return;
      setUploadProblem(
        asApiProblem(error, "The photo could not be uploaded. Please try again.").message,
      );
    } finally {
      if (isActiveDialog(session, memberId)) {
        setUploadingImage(false);
      }
    }
  };

  const handlePhotoUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setSelectedPhotoFile(file);
    event.target.value = "";
    void uploadSuggestionPhoto(file);
  };

  const handleSubmitSuggestion = async () => {
    if (!editingMember) return;
    const session = dialogSession.current;
    const memberId = editingMember.id;
    setSubmitting(true);
    setSuggestionProblem(null);
    try {
      await submitDirectForm(suggestForm);
      if (!isActiveDialog(session, memberId)) return;
      setShowSuccess(true);
      clearSuccessTimer();
      successTimer.current = setTimeout(() => {
        if (!isActiveDialog(session, memberId)) return;
        dialogSession.current += 1;
        dialogMemberId.current = null;
        successTimer.current = null;
        setShowSuccess(false);
        setEditingMember(null);
      }, 3000);
    } catch (error: unknown) {
      if (!isActiveDialog(session, memberId)) return;
      setSuggestionProblem(
        asApiProblem(error, "The suggestion could not be submitted. Please try again.")
          .message,
      );
    } finally {
      if (isActiveDialog(session, memberId)) {
        setSubmitting(false);
      }
    }
  };

  const closeSuggestion = () => {
    dialogSession.current += 1;
    dialogMemberId.current = null;
    clearSuccessTimer();
    setEditingMember(null);
    setShowSuccess(false);
    setSuggestionProblem(null);
    setUploadProblem(null);
    setSelectedPhotoFile(null);
    setSubmitting(false);
    setUploadingImage(false);
    suggestionTrigger.current?.focus();
  };

  const openSuggestion = (member: Member, trigger: HTMLButtonElement) => {
    dialogSession.current += 1;
    dialogMemberId.current = member.id;
    clearSuccessTimer();
    suggestionTrigger.current = trigger;
    setEditingMember(member);
    setShowSuccess(false);
    setSuggestionProblem(null);
    setUploadProblem(null);
    setSelectedPhotoFile(null);
    setSubmitting(false);
    setUploadingImage(false);
    setSuggestForm({
      fullName: member.FullName || "",
      fatherName: member.FatherName || "",
      motherName: member.MotherName || "",
      spouseName: member.SpouseName || "",
      dateOfBirth: member.DateOfBirth || "",
      dateOfDeath: member.DateOfDeath || "",
      location: member.CurrentCity || "",
      biography: member.Biography || "",
      gender: member.Gender || "Male",
      profileImage: member.ProfileImageUrl || "",
    });
  };

  const selectThreeDimensionalView = () => {
    const reducedMotion =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (reducedMotion) {
      setViewMode("2d");
      setEnhancementNotice(
        "3D motion is disabled by your preferences. The 2D family tree remains available.",
      );
      return;
    }

    const webglAvailable =
      typeof window.WebGLRenderingContext === "function" ||
      typeof window.WebGL2RenderingContext === "function";
    if (!webglAvailable) {
      setViewMode("2d");
      setEnhancementNotice(
        "3D view is unavailable. The 2D family tree remains available.",
      );
      return;
    }

    setEnhancementNotice(null);
    setViewMode("3d");
  };

  const selectTwoDimensionalView = () => {
    setEnhancementNotice(null);
    setViewMode("2d");
  };

  const handleThreeDimensionalUnavailable = useCallback(() => {
    setViewMode("2d");
    setEnhancementNotice(
      "3D view is unavailable. The 2D family tree remains available.",
    );
  }, []);

  return (
    <div className="mx-auto max-w-7xl px-5 sm:px-8 py-12 sm:py-16 overflow-x-auto rendering-wrapper relative">
      <div className="mb-12 animate-fadeInUp flex items-end justify-between">
        <div>
          <p className="text-accent text-sm font-medium uppercase tracking-wide mb-2 flex items-center gap-2">
            <span className="w-6 h-px bg-accent" />
            Genealogy
          </p>
          <h1 className="heading-serif text-3xl sm:text-4xl font-bold mb-3">
            Family Tree
          </h1>
          <p className="text-text-muted text-base max-w-lg">
            Explore generations of heritage. Scroll horizontally to view wide branches of the family.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <div
            className="flex items-center rounded-lg border border-border bg-bg-card p-1 shadow-sm"
            role="group"
            aria-label="Tree view"
          >
            <button
              type="button"
              aria-pressed={viewMode === "3d"}
              onClick={selectThreeDimensionalView}
              className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${viewMode === "3d" ? "bg-accent text-white" : "text-text-muted hover:text-text-primary"}`}
            >
              3D
            </button>
            <button
              type="button"
              aria-pressed={viewMode === "2d"}
              onClick={selectTwoDimensionalView}
              className={`px-3 py-1.5 text-xs font-semibold rounded-md transition-colors ${viewMode === "2d" ? "bg-accent text-white" : "text-text-muted hover:text-text-primary"}`}
            >
              2D
            </button>
          </div>
          {tree.length > 0 && (
            <Link href="/submit" className="btn-primary py-2 px-4 text-xs whitespace-nowrap hidden sm:flex">
              <Plus className="w-3.5 h-3.5" />
              Add Family Member
            </Link>
          )}
        </div>
      </div>

      {enhancementNotice && (
        <p
          role="status"
          className="mb-4 rounded-lg border border-border bg-bg-secondary px-4 py-3 text-sm text-text-muted"
        >
          {enhancementNotice}
        </p>
      )}

      <style dangerouslySetInnerHTML={{
        __html: `
        .family-tree { display: flex; justify-content: center; padding-bottom: 3rem; }
        .family-tree ul { padding-top: 30px; position: relative; transition: all 0.5s; display: flex; justify-content: center; }
        .family-tree li { float: left; text-align: center; list-style-type: none; position: relative; padding: 30px 10px 0 10px; transition: all 0.5s; }
        .family-tree li::before, .family-tree li::after { content: ''; position: absolute; top: 0; height: 30px; }
        /* Use --line-pos to point exactly to the blood child instead of the absolute center */
        .family-tree li::before { right: calc(100% - var(--line-pos, 50%)); width: var(--line-pos, 50%); border-top: 2px solid #c9a97a; }
        .family-tree li::after { left: var(--line-pos, 50%); width: calc(100% - var(--line-pos, 50%)); border-left: 2px solid #c9a97a; border-top: 2px solid #c9a97a; }
        .family-tree li:only-child::after, .family-tree li:only-child::before { display: none; }
        .family-tree li:only-child { padding-top: 0; }
        .family-tree li:first-child::before, .family-tree li:last-child::after { border: 0 none; }
        .family-tree li:last-child::before { border-right: 2px solid #c9a97a; border-radius: 0 5px 0 0; }
        .family-tree li:first-child::after { border-radius: 5px 0 0 0; }
        /* The drop-down line from the parent couple to the horizontal line. This always stays at 50% because the children branch from the union. */
        .family-tree ul ul::before { content: ''; position: absolute; top: 0; left: 50%; border-left: 2px solid #c9a97a; width: 0; height: 30px; transform: translateX(-1px); }
      `}} />

      <div
        className="relative h-[65vh] overflow-hidden rounded-lg border border-border bg-bg-secondary/30 animate-fadeInUp"
      >
        {treeState.status === "loading" && (
          <AsyncState state="loading" title="Loading family tree" />
        )}

        {treeState.status === "error" && (
          <AsyncState
            state="error"
            title="Tree unavailable"
            message={treeState.problem.message}
            actionLabel="Retry"
            onAction={retryTree}
          />
        )}

        {treeState.status === "empty" && (
          <div className="flex h-full flex-col items-center justify-center px-5 text-center">
            <div className="mx-auto mb-5 flex h-16 w-16 items-center justify-center rounded-lg bg-bg-secondary">
              <User className="h-7 w-7 text-text-light" />
            </div>
            <h2 className="font-serif text-xl font-semibold mb-2">No family members yet</h2>
            <p className="text-text-muted text-sm mb-7 max-w-sm mx-auto">
              Start building your family tree by adding the very first member of your heritage.
            </p>
            <Link href="/submit" className="btn-primary">
              <Plus className="w-4 h-4" />
              Add First Member
            </Link>
          </div>
        )}

        {treeState.status === "ready" && viewMode === "3d" && (
          <div className="absolute inset-0 z-10">
            <Suspense fallback={<AsyncState state="loading" title="Loading 3D family tree" />}>
              <FamilyTree3D
                tree={tree}
                onUnavailable={handleThreeDimensionalUnavailable}
              />
            </Suspense>
          </div>
        )}

        {treeState.status === "ready" && viewMode === "2d" && (
          <TransformWrapper
            initialScale={1}
            minScale={0.2}
            maxScale={4}
            centerOnInit={true}
            wheel={{ step: 0.1 }}
          >
            {({ zoomIn, zoomOut, resetTransform }) => (
              <>
                <div className="absolute top-4 right-4 z-10 flex flex-col gap-1.5 bg-white/90 backdrop-blur-sm p-1.5 rounded-lg shadow-sm border border-border">
                  <button type="button" aria-label="Zoom in" onClick={() => zoomIn()} className="p-2 hover:bg-bg-secondary rounded-lg text-text-muted hover:text-text-primary transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent" title="Zoom In">
                    <ZoomIn className="w-5 h-5" />
                  </button>
                  <button type="button" aria-label="Zoom out" onClick={() => zoomOut()} className="p-2 hover:bg-bg-secondary rounded-lg text-text-muted hover:text-text-primary transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent" title="Zoom Out">
                    <ZoomOut className="w-5 h-5" />
                  </button>
                  <div className="h-px bg-border/50 w-full my-0.5"></div>
                  <button type="button" aria-label="Reset view" onClick={() => resetTransform()} className="p-2 hover:bg-bg-secondary rounded-lg text-text-muted hover:text-text-primary transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent" title="Reset View">
                    <Maximize className="w-4 h-4" />
                  </button>
                </div>
                
                <TransformComponent wrapperStyle={{ width: "100%", height: "100%", cursor: "grab" }}>
                  <div className="family-tree pt-16 pb-24 px-12 min-w-max">
                    <ul>
                      {tree.map((root) => (
                        <FamilyTreeNode
                          key={root.id}
                          member={root}
                          onSuggestEdit={openSuggestion}
                        />
                      ))}
                    </ul>
                  </div>
                </TransformComponent>
              </>
            )}
          </TransformWrapper>
        )}
      </div>
      {editingMember && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-fadeIn">
          <div role="dialog" aria-modal="true" aria-labelledby="suggestion-dialog-title" className="bg-white rounded-lg shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col overflow-hidden animate-fadeInUp">
            <div className="flex items-center justify-between p-4 border-b border-border bg-bg-secondary sticky top-0 shrink-0">
              <h3 id="suggestion-dialog-title" className="font-serif font-bold text-lg text-text-primary">Suggest an Update</h3>
              <button type="button" aria-label="Close suggestion" disabled={submitting} onClick={closeSuggestion} className="p-1 text-text-muted hover:text-text-primary focus-visible:outline-2 focus-visible:outline-accent">
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <div className="p-6 overflow-y-auto space-y-5 custom-scrollbar bg-bg-primary">
              {showSuccess ? (
                <div className="py-12 text-center">
                  <div className="w-16 h-16 bg-emerald/10 text-emerald rounded-full flex items-center justify-center mx-auto mb-4">
                    <Save className="w-8 h-8" />
                  </div>
                  <h4 className="text-xl font-bold text-text-primary mb-2">Suggestion Sent!</h4>
                  <p className="text-text-muted text-sm">Thank you. Your suggestion has been sent to the admin for approval.</p>
                </div>
              ) : (
                <>
                  {(suggestionProblem || uploadProblem) && (
                    <div role="alert" className="rounded-lg border border-terracotta-light bg-terracotta-light/30 p-3 text-sm text-terracotta">
                      {uploadProblem ?? suggestionProblem}
                    </div>
                  )}
                  <div className="bg-sky/5 p-4 rounded-lg border border-sky/20 mb-4 text-xs text-sky-900 leading-relaxed italic">
                    Note: Your changes will appear on the tree once they are reviewed and approved by the family administrator.
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                    <div className="sm:col-span-2">
                      <label htmlFor="suggest-full-name" className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Full Name</label>
                      <input id="suggest-full-name" type="text" value={suggestForm.fullName || ""} onChange={(e) => setSuggestForm({ ...suggestForm, fullName: e.target.value })} className="input-heritage w-full" />
                    </div>

                    <div>
                      <label htmlFor="suggest-birth-year" className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Birth Year</label>
                      <input id="suggest-birth-year" type="text" value={suggestForm.dateOfBirth || ""} onChange={(e) => setSuggestForm({ ...suggestForm, dateOfBirth: e.target.value })} className="input-heritage w-full" placeholder="e.g. 1950" />
                    </div>
                    <div>
                      <label htmlFor="suggest-death-year" className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Death Year (Leave blank if alive)</label>
                      <input id="suggest-death-year" type="text" value={suggestForm.dateOfDeath || ""} onChange={(e) => setSuggestForm({ ...suggestForm, dateOfDeath: e.target.value })} className="input-heritage w-full" placeholder="e.g. 2024" />
                    </div>

                    <div>
                      <label htmlFor="suggest-father-name" className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Father&apos;s Name</label>
                      <input id="suggest-father-name" type="text" value={suggestForm.fatherName || ""} onChange={(e) => setSuggestForm({ ...suggestForm, fatherName: e.target.value })} className="input-heritage w-full" />
                    </div>
                    <div>
                      <label htmlFor="suggest-mother-name" className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Mother&apos;s Name</label>
                      <input id="suggest-mother-name" type="text" value={suggestForm.motherName || ""} onChange={(e) => setSuggestForm({ ...suggestForm, motherName: e.target.value })} className="input-heritage w-full" />
                    </div>

                    <div>
                      <label htmlFor="suggest-spouse-name" className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Spouse&apos;s Name</label>
                      <input id="suggest-spouse-name" type="text" value={suggestForm.spouseName || ""} onChange={(e) => setSuggestForm({ ...suggestForm, spouseName: e.target.value })} className="input-heritage w-full" />
                    </div>
                    <div>
                      <label htmlFor="suggest-home-city" className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Home City</label>
                      <input id="suggest-home-city" type="text" value={suggestForm.location || ""} onChange={(e) => setSuggestForm({ ...suggestForm, location: e.target.value })} className="input-heritage w-full" />
                    </div>

                    <div className="sm:col-span-2">
                      <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Profile Photo</label>
                      {suggestForm.profileImage ? (
                        <div className="flex items-center gap-4 p-3 border border-border rounded-lg bg-bg-primary">
                          <div role="img" aria-label="Profile preview" className="h-12 w-12 rounded-full bg-cover bg-center" style={{ backgroundImage: `url(${suggestForm.profileImage})` }} />
                          <button
                            type="button"
                            onClick={() => {
                              setSuggestForm({ ...suggestForm, profileImage: "" });
                              setSelectedPhotoFile(null);
                              setUploadProblem(null);
                            }}
                            className="text-xs text-terracotta hover:underline"
                          >
                            Remove
                          </button>
                        </div>
                      ) : (
                        <div className="relative">
                          <input aria-label="Upload profile photo" type="file" accept="image/*" onChange={handlePhotoUpload} disabled={uploadingImage} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer focus-visible:opacity-100" />
                          <div className={`w-full px-4 py-2.5 rounded-lg border border-dashed border-border flex items-center justify-center gap-2 hover:border-accent transition-all ${uploadingImage ? 'opacity-50' : ''}`}>
                            {uploadingImage ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                            <span className="text-sm font-medium text-text-muted">Upload Photo</span>
                          </div>
                          {selectedPhotoFile && uploadProblem && (
                            <button
                              type="button"
                              onClick={() => void uploadSuggestionPhoto(selectedPhotoFile)}
                              disabled={uploadingImage}
                              className="btn-secondary mt-2 w-full"
                            >
                              Retry Upload
                            </button>
                          )}
                        </div>
                      )}
                    </div>

                    <div className="sm:col-span-2">
                      <label htmlFor="suggest-biography" className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Brief Biography / Notes</label>
                      <textarea id="suggest-biography" rows={4} value={suggestForm.biography || ""} onChange={(e) => setSuggestForm({ ...suggestForm, biography: e.target.value })} className="input-heritage w-full resize-y" />
                    </div>
                  </div>
                </>
              )}
            </div>
 
            <div className="p-4 border-t border-border bg-bg-secondary flex justify-end gap-3 sticky bottom-0 shrink-0">
              <button type="button" disabled={submitting} onClick={closeSuggestion} className="px-5 py-2.5 text-sm font-semibold text-text-muted hover:text-text-primary rounded-lg transition-colors">Cancel</button>
              {!showSuccess && (
                <button 
                  type="button"
                  onClick={handleSubmitSuggestion}
                  disabled={submitting || uploadingImage}
                  className="btn-primary"
                >
                  {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                  Submit Suggestion
                </button>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

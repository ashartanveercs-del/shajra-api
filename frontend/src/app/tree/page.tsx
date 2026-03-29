"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchTree, submitDirectForm, uploadImage, type Member } from "@/lib/api";
import { User, Heart, Loader2, Plus, ZoomIn, ZoomOut, Maximize, Edit3, X, Save } from "lucide-react";
import { TransformWrapper, TransformComponent } from "react-zoom-pan-pinch";

function TreeCard({ member, onSuggestEdit }: { member: Member & { Spouse?: Member }, onSuggestEdit: (m: Member) => void }) {
  const isCouple = !!member.Spouse;
  
  const getGenderBg = (gender?: string) => {
    if (gender === "Male") return "bg-sky-light/80 border-sky/30 text-sky-900";
    if (gender === "Female") return "bg-plum-light/80 border-plum/30 text-plum-900";
    return "bg-bg-secondary border-border text-text-primary";
  };

  const AvatarCircle = ({ m, label }: { m: Member, label?: string }) => (
    <div className="flex flex-col items-center">
      <div
        className="w-10 h-10 sm:w-12 sm:h-12 rounded-full bg-white/70 flex items-center justify-center text-base sm:text-lg font-serif font-bold mb-1.5 bg-cover bg-center relative z-10 border border-black/5 shadow-sm"
        style={m.ProfileImageUrl ? { backgroundImage: `url(${m.ProfileImageUrl})` } : {}}
      >
        {!m.ProfileImageUrl && (m.FullName || "?")[0]}
        {m.IsAlive && (
          <div className="absolute -top-1 -right-1 z-20 w-3.5 h-3.5 rounded-full bg-white flex items-center justify-center border border-emerald/20 shadow-sm">
            <Heart className="w-2 h-2 text-emerald fill-emerald" />
          </div>
        )}
      </div>
      <div className="font-semibold text-[11px] sm:text-xs leading-none max-w-[80px] truncate text-center opacity-90">
        {m.FullName?.split(" ")[0]}
      </div>
    </div>
  );

  return (
    <div className="group inline-block">
      <div
        className={`flex flex-col items-center p-3 rounded-2xl border ${getGenderBg(member.Gender)} shadow-sm hover:shadow-md transition-all min-w-[120px] relative hover:-translate-y-1 ${member.FullName === "Ashar Tanveer" ? 'border-accent shadow-[0_0_15px_rgba(231,166,26,0.3)] ring-2 ring-accent/20' : ''} ${member.FullName?.includes("(Unknown)") ? 'border-dashed opacity-70 grayscale-[0.3]' : ''}`}
      >
        {/* Creator Badge */}
        {member.FullName === "Ashar Tanveer" && (
          <div className="absolute -top-3 left-1/2 -translate-x-1/2 bg-accent text-white px-3 py-0.5 rounded-full text-[10px] font-bold tracking-widest shadow-lg border border-white/20 animate-pulse z-[110]">
            CREATOR
          </div>
        )}
        <button 
          onClick={(e) => {
            e.preventDefault();
            onSuggestEdit(member);
          }}
          className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-all flex items-center gap-1.5 p-1.5 bg-white/90 backdrop-blur rounded-lg text-text-primary shadow-lg border border-accent/20 z-30 hover:bg-white"
          title="Suggest an improvement"
        >
          <Edit3 className="w-3.5 h-3.5" />
          <span className="text-[9px] font-bold uppercase tracking-tighter">Suggest Edit</span>
        </button>

        <div className="flex items-start gap-4 mb-2">
          <AvatarCircle m={member} />
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
                <AvatarCircle m={member.Spouse} />
              </div>
            </>
          )}
        </div>

        {isCouple ? (
          <div className="text-[10px] uppercase tracking-tighter font-bold opacity-40 mt-1">
             Family Unit 
          </div>
        ) : (
          (member.DateOfBirth || member.DateOfDeath) && (
            <div className="text-[9px] opacity-70 mt-0.5 whitespace-nowrap">
              {member.DateOfBirth || "?"} &mdash; {member.DateOfDeath || "Present"}
            </div>
          )
        )}
      </div>
    </div>
  );
}

function FamilyTreeNode({ member, onSuggestEdit }: { member: Member, onSuggestEdit: (m: Member) => void }) {
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
  const [tree, setTree] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  
  const [editingMember, setEditingMember] = useState<Member | null>(null);
  const [suggestForm, setSuggestForm] = useState<any>({});
  const [submitting, setSubmitting] = useState(false);
  const [uploadingImage, setUploadingImage] = useState(false);
  const [showSuccess, setShowSuccess] = useState(false);

  const loadTree = () => {
    setLoading(true);
    fetchTree()
      .then(setTree)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadTree();
  }, []);

  const handlePhotoUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadingImage(true);
    try {
      const data = await uploadImage(file);
      setSuggestForm({ ...suggestForm, profileImage: data.url });
    } catch (err: any) {
      alert(err.message || "Failed to upload image.");
    } finally {
      setUploadingImage(false);
    }
  };

  const handleSubmitSuggestion = async () => {
    setSubmitting(true);
    try {
      await submitDirectForm(suggestForm);
      setShowSuccess(true);
      setTimeout(() => {
        setShowSuccess(false);
        setEditingMember(null);
      }, 3000);
    } catch (e: any) {
      alert("Failed to submit: " + e.message);
    }
    setSubmitting(false);
  };

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
        {tree.length > 0 && (
          <Link href="/submit" className="btn-primary py-2 px-4 text-xs whitespace-nowrap hidden sm:flex">
            <Plus className="w-3.5 h-3.5" />
            Add Family Member
          </Link>
        )}
      </div>

      <style dangerouslySetInnerHTML={{
        __html: `
        .family-tree { display: flex; justify-content: center; padding-bottom: 3rem; }
        .family-tree ul { padding-top: 30px; position: relative; transition: all 0.5s; display: flex; justify-content: center; }
        .family-tree li { float: left; text-align: center; list-style-type: none; position: relative; padding: 30px 10px 0 10px; transition: all 0.5s; }
        .family-tree li::before, .family-tree li::after { content: ''; position: absolute; top: 0; height: 30px; }
        /* Use --line-pos to point exactly to the blood child instead of the absolute center */
        .family-tree li::before { right: calc(100% - var(--line-pos, 50%)); width: var(--line-pos, 50%); border-top: 2px solid #dcd7cf; }
        .family-tree li::after { left: var(--line-pos, 50%); width: calc(100% - var(--line-pos, 50%)); border-left: 2px solid #dcd7cf; border-top: 2px solid #dcd7cf; }
        .family-tree li:only-child::after, .family-tree li:only-child::before { display: none; }
        .family-tree li:only-child { padding-top: 0; }
        .family-tree li:first-child::before, .family-tree li:last-child::after { border: 0 none; }
        .family-tree li:last-child::before { border-right: 2px solid #dcd7cf; border-radius: 0 5px 0 0; }
        .family-tree li:first-child::after { border-radius: 5px 0 0 0; }
        /* The drop-down line from the parent couple to the horizontal line. This always stays at 50% because the children branch from the union. */
        .family-tree ul ul::before { content: ''; position: absolute; top: 0; left: 50%; border-left: 2px solid #dcd7cf; width: 0; height: 30px; transform: translateX(-1px); }
      `}} />

      {loading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="w-6 h-6 animate-spin text-accent" />
        </div>
      )}

      {!loading && !error && tree.length === 0 && (
        <div className="heritage-card p-14 text-center animate-fadeInUp">
          <div className="w-16 h-16 mx-auto mb-5 rounded-2xl bg-bg-secondary flex items-center justify-center">
            <User className="w-7 h-7 text-text-light" />
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

      {!loading && tree.length > 0 && (
        <div className="relative animate-fadeInUp bg-bg-secondary/30 rounded-3xl border border-border overflow-hidden" style={{ height: "65vh" }}>
          <TransformWrapper
            initialScale={1}
            minScale={0.2}
            maxScale={4}
            centerOnInit={true}
            wheel={{ step: 0.1 }}
          >
            {({ zoomIn, zoomOut, resetTransform }) => (
              <>
                <div className="absolute top-4 right-4 z-10 flex flex-col gap-1.5 bg-white/90 backdrop-blur-sm p-1.5 rounded-xl shadow-sm border border-border">
                  <button onClick={() => zoomIn()} className="p-2 hover:bg-bg-secondary rounded-lg text-text-muted hover:text-text-primary transition-colors" title="Zoom In">
                    <ZoomIn className="w-5 h-5" />
                  </button>
                  <button onClick={() => zoomOut()} className="p-2 hover:bg-bg-secondary rounded-lg text-text-muted hover:text-text-primary transition-colors" title="Zoom Out">
                    <ZoomOut className="w-5 h-5" />
                  </button>
                  <div className="h-px bg-border/50 w-full my-0.5"></div>
                  <button onClick={() => resetTransform()} className="p-2 hover:bg-bg-secondary rounded-lg text-text-muted hover:text-text-primary transition-colors" title="Reset View">
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
                          onSuggestEdit={(m) => {
                            setEditingMember(m);
                            setSuggestForm({
                              fullName: m.FullName,
                              fatherName: m.FatherName || "",
                              motherName: m.MotherName || "",
                              spouseName: m.SpouseName || "",
                              dateOfBirth: m.DateOfBirth || "",
                              dateOfDeath: m.DateOfDeath || "",
                              location: m.CurrentCity || "",
                              biography: m.Biography || "",
                              gender: m.Gender || "Male",
                              profileImage: m.ProfileImageUrl || "",
                            });
                          }}
                        />
                      ))}
                    </ul>
                  </div>
                </TransformComponent>
              </>
            )}
          </TransformWrapper>
        </div>
      )}
      {editingMember && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-fadeIn">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col overflow-hidden animate-fadeInUp">
            <div className="flex items-center justify-between p-4 border-b border-border bg-bg-secondary sticky top-0 shrink-0">
              <h3 className="font-serif font-bold text-lg text-text-primary">Suggest an Update</h3>
              <button disabled={submitting} onClick={() => setEditingMember(null)} className="p-1 text-text-muted hover:text-text-primary">
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
                  <div className="bg-sky/5 p-4 rounded-xl border border-sky/20 mb-4 text-xs text-sky-900 leading-relaxed italic">
                    Note: Your changes will appear on the tree once they are reviewed and approved by the family administrator.
                  </div>

                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                    <div className="sm:col-span-2">
                      <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Full Name</label>
                      <input type="text" value={suggestForm.fullName || ""} onChange={(e) => setSuggestForm({ ...suggestForm, fullName: e.target.value })} className="input-heritage w-full" />
                    </div>

                    <div>
                      <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Birth Year</label>
                      <input type="text" value={suggestForm.dateOfBirth || ""} onChange={(e) => setSuggestForm({ ...suggestForm, dateOfBirth: e.target.value })} className="input-heritage w-full" placeholder="e.g. 1950" />
                    </div>
                    <div>
                      <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Death Year (Leave blank if alive)</label>
                      <input type="text" value={suggestForm.dateOfDeath || ""} onChange={(e) => setSuggestForm({ ...suggestForm, dateOfDeath: e.target.value })} className="input-heritage w-full" placeholder="e.g. 2024" />
                    </div>

                    <div>
                      <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Father's Name</label>
                      <input type="text" value={suggestForm.fatherName || ""} onChange={(e) => setSuggestForm({ ...suggestForm, fatherName: e.target.value })} className="input-heritage w-full" />
                    </div>
                    <div>
                      <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Mother's Name</label>
                      <input type="text" value={suggestForm.motherName || ""} onChange={(e) => setSuggestForm({ ...suggestForm, motherName: e.target.value })} className="input-heritage w-full" />
                    </div>

                    <div>
                      <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Spouse's Name</label>
                      <input type="text" value={suggestForm.spouseName || ""} onChange={(e) => setSuggestForm({ ...suggestForm, spouseName: e.target.value })} className="input-heritage w-full" />
                    </div>
                    <div>
                      <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Home City</label>
                      <input type="text" value={suggestForm.location || ""} onChange={(e) => setSuggestForm({ ...suggestForm, location: e.target.value })} className="input-heritage w-full" />
                    </div>

                    <div className="sm:col-span-2">
                      <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Profile Photo</label>
                      {suggestForm.profileImage ? (
                        <div className="flex items-center gap-4 p-3 border border-border rounded-lg bg-bg-primary">
                          <img src={suggestForm.profileImage} alt="Preview" className="w-12 h-12 rounded-full object-cover" />
                          <button type="button" onClick={() => setSuggestForm({ ...suggestForm, profileImage: "" })} className="text-xs text-terracotta hover:underline">Remove</button>
                        </div>
                      ) : (
                        <div className="relative">
                          <input type="file" accept="image/*" onChange={handlePhotoUpload} disabled={uploadingImage} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer" />
                          <div className={`w-full px-4 py-2.5 rounded-lg border border-dashed border-border flex items-center justify-center gap-2 hover:border-accent transition-all ${uploadingImage ? 'opacity-50' : ''}`}>
                            {uploadingImage ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
                            <span className="text-sm font-medium text-text-muted">Upload Photo</span>
                          </div>
                        </div>
                      )}
                    </div>

                    <div className="sm:col-span-2">
                      <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Brief Biography / Notes</label>
                      <textarea rows={4} value={suggestForm.biography || ""} onChange={(e) => setSuggestForm({ ...suggestForm, biography: e.target.value })} className="input-heritage w-full resize-y" />
                    </div>
                  </div>
                </>
              )}
            </div>
 
            <div className="p-4 border-t border-border bg-bg-secondary flex justify-end gap-3 sticky bottom-0 shrink-0">
              <button disabled={submitting} onClick={() => setEditingMember(null)} className="px-5 py-2.5 text-sm font-semibold text-text-muted hover:text-text-primary rounded-lg transition-colors">Cancel</button>
              {!showSuccess && (
                <button 
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

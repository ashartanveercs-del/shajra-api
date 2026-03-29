"use client";

import { useEffect, useState } from "react";
import { fetchTree, adminUpdateMember, adminDeleteMember, adminCreateMember, uploadImage, type Member } from "@/lib/api";
import { Heart, Loader2, X, Save, GripHorizontal, Edit3, Trash2, Link2Off, UserPlus } from "lucide-react";

function AdminTreeCard({
  member,
  onNodeClick,
  onDropNode,
  onDelete,
  onUnlink,
  onAddChild,
}: {
  member: Member & { Spouse?: Member };
  onNodeClick: (m: Member) => void;
  onDropNode: (draggedId: string, targetId: string) => void;
  onDelete: (id: string) => void;
  onUnlink: (id: string) => void;
  onAddChild: (m: Member) => void;
}) {
  const isCouple = !!member.Spouse;
  const [isOver, setIsOver] = useState(false);
  
  // Parse CardStyle JSON for custom per-member styling
  const parseCardStyle = (m: Member) => {
    try {
      return m.CardStyle ? JSON.parse(m.CardStyle) : null;
    } catch { return null; }
  };

  const cardStyle = parseCardStyle(member);
  const spouseCardStyle = member.Spouse ? parseCardStyle(member.Spouse) : null;

  const getGenderBg = (gender?: string) => {
    if (gender === "Male") return "bg-sky-light/80 border-sky/30 text-sky-900";
    if (gender === "Female") return "bg-plum-light/80 border-plum/30 text-plum-900";
    return "bg-bg-secondary border-border text-text-primary";
  };

  const handleDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData("text/plain", member.id);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsOver(true);
    e.dataTransfer.dropEffect = "move";
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsOver(false);
    const draggedId = e.dataTransfer.getData("text/plain");
    if (draggedId && draggedId !== member.id) {
      onDropNode(draggedId, member.id);
    }
  };

  // Render a single person within the card
  const PersonCard = ({ m, style }: { m: Member, style: any }) => {
    const isCreator = (m.FullName || "").trim().includes("Ashar Tanveer");
    const badge = style?.badge || (isCreator ? "CREATOR" : null);
    const isPlaceholderPerson = m.IsPlaceholder || (m.FullName || "").includes("(Unknown)");
    
    return (
      <div
        className="flex flex-col items-center min-w-[90px] cursor-pointer relative"
        onClick={(e) => { e.stopPropagation(); onNodeClick(m); }}
      >
        {badge && (
          <div
            className="absolute -top-3 left-1/2 -translate-x-1/2 text-white px-2.5 py-0.5 rounded-full text-[9px] font-bold tracking-widest shadow-lg z-[110] whitespace-nowrap"
            style={{ background: style?.badgeColor || 'var(--accent)' }}
          >
            {badge}
          </div>
        )}
        <div
          className={`w-10 h-10 sm:w-12 sm:h-12 rounded-full bg-white/70 flex items-center justify-center text-base sm:text-lg font-serif font-bold mb-1 bg-cover bg-center relative z-10 border shadow-sm ${isPlaceholderPerson ? 'border-dashed border-text-light/40 opacity-60' : 'border-black/5'}`}
          style={m.ProfileImageUrl ? { backgroundImage: `url(${m.ProfileImageUrl})` } : {}}
        >
          {!m.ProfileImageUrl && (m.FullName || "?")[0]}
          {m.IsAlive && (
            <div className="absolute -top-1 -right-1 z-20 w-3.5 h-3.5 rounded-full bg-white flex items-center justify-center border border-emerald/20 shadow-sm">
              <Heart className="w-2 h-2 text-emerald fill-emerald" />
            </div>
          )}
        </div>
        <div className="font-semibold text-[11px] sm:text-xs leading-tight max-w-[100px] text-center opacity-90 line-clamp-2">
          {m.FullName?.split(" ").slice(0, 2).join(" ")}
        </div>
        {m.CurrentCity && (
          <div className="text-[8px] opacity-40 mt-0.5 truncate max-w-[90px]">{m.CurrentCity}</div>
        )}
      </div>
    );
  };

  const customInlineStyles: React.CSSProperties = {};
  if (cardStyle?.bg) customInlineStyles.background = cardStyle.bg;
  if (cardStyle?.borderColor) customInlineStyles.borderColor = cardStyle.borderColor;
  if (cardStyle?.glow) customInlineStyles.boxShadow = cardStyle.glow;

  const isCreator = (member.FullName || "").trim().includes("Ashar Tanveer") || 
                    (member.Spouse?.FullName || "").trim().includes("Ashar Tanveer");
  const isPlaceholder = member.IsPlaceholder || (member.FullName || "").includes("(Unknown)");

  return (
    <div className="group inline-block">
      <div
        draggable
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        onDragLeave={handleDragLeave}
        className={`flex flex-col items-center p-3 rounded-2xl border ${getGenderBg(member.Gender)} shadow-sm hover:shadow-md transition-all min-w-[120px] relative cursor-grab active:cursor-grabbing ring-2 ${isOver ? 'ring-accent border-accent scale-105 shadow-lg' : 'ring-transparent'} hover:ring-accent/50 group-hover:-translate-y-1 ${isCreator ? 'border-accent shadow-[0_0_15px_rgba(231,166,26,0.3)] ring-accent/30' : ''} ${isPlaceholder ? 'border-dashed opacity-70' : ''} ${cardStyle?.className || ''}`}
        style={customInlineStyles}
      >
        <div className="absolute top-1 left-1 opacity-0 group-hover:opacity-100 transition-all flex items-center gap-1 p-1 bg-white/95 backdrop-blur rounded-lg text-text-primary shadow-xl z-[100] border border-sky/20">
          <button onClick={(e) => { e.stopPropagation(); onNodeClick(member); }} className="p-1 hover:bg-sky/10 rounded text-sky-700" title={`Edit ${member.FullName}`}>
            <Edit3 className="w-3.5 h-3.5" />
          </button>
          <button onClick={(e) => { e.stopPropagation(); onAddChild(member); }} className="p-1 hover:bg-emerald/10 rounded text-emerald" title="Add Child">
            <UserPlus className="w-3.5 h-3.5" />
          </button>
          <button onClick={(e) => { e.stopPropagation(); onDelete(member.id); }} className="p-1 hover:bg-terracotta/10 rounded text-terracotta" title="Delete">
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>

        {member.Spouse && (
          <div className="absolute top-1 right-1 opacity-0 group-hover:opacity-100 transition-all flex items-center gap-1 p-1 bg-white/95 backdrop-blur rounded-lg text-text-primary shadow-xl z-[100] border border-plum/20">
            <button onClick={(e) => { e.stopPropagation(); onNodeClick(member.Spouse as Member); }} className="p-1 hover:bg-plum/10 rounded text-plum-700" title={`Edit ${member.Spouse.FullName}`}>
              <Edit3 className="w-3.5 h-3.5" />
            </button>
            <button onClick={(e) => { e.stopPropagation(); onUnlink(member.id); }} className="p-1 hover:bg-black/5 rounded text-text-muted" title="Unlink Marriage">
              <Link2Off className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        <div className="flex items-start gap-3 mb-1">
          <PersonCard m={member} style={cardStyle} />
          {member.Spouse && (
            <>
              <div className="h-10 sm:h-12 flex items-center justify-center">
                <div className="w-4 h-px bg-border/50 relative">
                  <Heart className="w-2.5 h-2.5 text-plum absolute -top-1 left-1/2 -translate-x-1/2 fill-plum/20" />
                </div>
              </div>
              <PersonCard m={member.Spouse} style={spouseCardStyle} />
            </>
          )}
        </div>

        {isCouple && (
          <div className="text-[10px] uppercase tracking-tighter font-bold opacity-40 mt-1">
             Family Unit 
          </div>
        )}
      </div>
    </div>
  );
}

function AdminFamilyTreeNode({
  member,
  onNodeClick,
  onDropNode,
  onDelete,
  onUnlink,
  onAddChild,
}: {
  member: Member;
  onNodeClick: (m: Member) => void;
  onDropNode: (draggedId: string, targetId: string) => void;
  onDelete: (id: string) => void;
  onUnlink: (id: string) => void;
  onAddChild: (m: Member) => void;
}) {
  const isCouple = !!member.Spouse;
  // If married, shift the line leftwards to align exactly with the primary blood descendant.
  // The center of the primary card happens to be ~64px to the left of the couple box center.
  const linePos = isCouple ? 'calc(50% - 64px)' : '50%';

  return (
    <li style={{ '--line-pos': linePos } as React.CSSProperties}>
      <AdminTreeCard
        member={member}
        onNodeClick={onNodeClick}
        onDropNode={onDropNode}
        onDelete={onDelete}
        onUnlink={onUnlink}
        onAddChild={onAddChild}
      />
      {member.children && member.children.length > 0 && (
        <ul>
          {member.children.map((child) => (
            <AdminFamilyTreeNode
              key={child.id}
              member={child}
              onNodeClick={onNodeClick}
              onDropNode={onDropNode}
              onDelete={onDelete}
              onUnlink={onUnlink}
              onAddChild={onAddChild}
            />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function AdminTreeEditor({ token, onUpdated }: { token: string; onUpdated?: () => void }) {
  const [tree, setTree] = useState<Member[]>([]);
  
  const flattenTree = (nodes: Member[]): Member[] => {
    let list: Member[] = [];
    nodes.forEach(n => {
      // Add the member
      list.push(n);
      
      // PERPETUAL SPOUSE FIX: Include the spouse in the flattened list
      // so they appear in Mother/Father dropdowns.
      if (n.Spouse) {
        list.push(n.Spouse as Member);
      }
      
      // Recurse
      if(n.children) list = list.concat(flattenTree(n.children));
    });
    // Deduplicate just in case (e.g. if spouse is also a root, though rare)
    return Array.from(new Map(list.map(item => [item.id, item])).values());
  };

  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [editingMember, setEditingMember] = useState<Member | null>(null);
  const [editForm, setEditForm] = useState<any>({});
  const [uploadingImage, setUploadingImage] = useState(false);

  const handlePhotoUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    
    setUploadingImage(true);
    try {
      const data = await uploadImage(file);
      setEditForm({ ...editForm, ProfileImageUrl: data.url });
    } catch (err: any) {
      alert(err.message || "Failed to upload image.");
    } finally {
      setUploadingImage(false);
    }
  };

  const loadData = () => {
    setLoading(true);
    fetchTree().then(setTree).catch(() => {}).finally(() => setLoading(false));
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleDropNode = async (draggedId: string, targetId: string) => {
    setActionLoading(true);
    try {
      const allMembers = flattenTree(tree);
      const target = allMembers.find(m => m.id === targetId);
      
      let updates: any = {};
      
      if (target) {
        // 1. If target is a couple, set both parents
        if (target.Spouse) {
          if (target.Gender === "Male") {
            updates.FatherRecordId = target.id;
            updates.MotherRecordId = target.Spouse.id;
          } else {
            updates.MotherRecordId = target.id;
            updates.FatherRecordId = target.Spouse.id;
          }
        } 
        // 2. Otherwise, check gender
        else if (target.Gender === "Male") {
          updates.FatherRecordId = target.id;
        } else if (target.Gender === "Female") {
          updates.MotherRecordId = target.id;
        } else {
          // Default to Father if gender is unknown/unspecified
          updates.FatherRecordId = target.id;
        }
      }

      await adminUpdateMember(token, draggedId, updates);
      loadData();
      if (onUpdated) onUpdated();
    } catch (e: any) {
      alert("Failed to update structure: " + e.message);
    }
    setActionLoading(false);
  };

  const handleSaveEdit = async () => {
    if (!editingMember) return;
    setActionLoading(true);
    try {
      await adminUpdateMember(token, editingMember.id, editForm);
      setEditingMember(null);
      loadData();
      if (onUpdated) onUpdated();
    } catch (e: any) {
      alert("Failed to save changes: " + e.message);
    }
    setActionLoading(false);
  };

  const handleUnlinkMember = async (id: string) => {
    setActionLoading(true);
    try {
      await adminUpdateMember(token, id, { FatherRecordId: "", MotherRecordId: "" });
      await loadData();
      if (onUpdated) onUpdated();
    } catch (e: any) {
      alert("Failed to unlink: " + e.message);
    }
    setActionLoading(false);
  };

  const handleDeleteMember = async (id: string) => {
    const allMembers = flattenTree(tree);
    const target = allMembers.find(m => m.id === id);
    const name = target?.FullName || id;
    if (!confirm(`Are you sure you want to delete "${name}"? This cannot be undone easily.`)) return;
    setActionLoading(true);
    try {
      await adminDeleteMember(token, id);
      loadData();
      if (onUpdated) onUpdated();
    } catch (e: any) {
      alert("Failed to delete member: " + (e.message || "Make sure no children are tied to this person."));
    }
    setActionLoading(false);
  };

  const handleAddMember = () => {
    setEditingMember({ id: "new", FullName: "" });
    setEditForm({
      FullName: "",
      FatherName: "",
      MotherName: "",
      SpouseName: "",
      Gender: "Male",
      IsAlive: true,
      ProfileImageUrl: "",
    });
  };

  const handleCreateNew = async () => {
    setActionLoading(true);
    try {
      await adminCreateMember(token, editForm);
      setEditingMember(null);
      loadData();
      if (onUpdated) onUpdated();
    } catch (e: any) {
      alert("Failed to create member: " + e.message);
    }
    setActionLoading(false);
  };

  return (
    <div className="relative">
      <style dangerouslySetInnerHTML={{
        __html: `
        .family-tree { display: block; padding-bottom: 3rem; }
        .family-tree > ul { padding-top: 0; display: flex; justify-content: center; width: max-content; margin: 0 auto; }
        .family-tree ul { padding-top: 30px; position: relative; transition: all 0.5s; display: flex; justify-content: center; }
        .family-tree li { float: left; text-align: center; list-style-type: none; position: relative; padding: 30px 10px 0 10px; transition: all 0.5s; }
        .family-tree li::before, .family-tree li::after { content: ''; position: absolute; top: 0; height: 30px; }
        /* Use --line-pos to point exactly to the blood child instead of the absolute center */
        .family-tree li::before { right: calc(100% - var(--line-pos, 50%)); width: var(--line-pos, 50%); border-top: 2px solid #cbd5e1; }
        .family-tree li::after { left: var(--line-pos, 50%); width: calc(100% - var(--line-pos, 50%)); border-left: 2px solid #cbd5e1; border-top: 2px solid #cbd5e1; }
        .family-tree li:only-child::after, .family-tree li:only-child::before { display: none; }
        .family-tree li:only-child { padding-top: 0; }
        .family-tree li:first-child::before, .family-tree li:last-child::after { border: 0 none; }
        .family-tree li:last-child::before { border-right: 2px solid #cbd5e1; border-radius: 0 5px 0 0; }
        .family-tree li:first-child::after { border-radius: 5px 0 0 0; }
        /* The drop-down line from the parent couple to the horizontal line. This always stays at 50% because the children branch from the union. */
        .family-tree ul ul::before { content: ''; position: absolute; top: 0; left: 50%; border-left: 2px solid #cbd5e1; width: 0; height: 30px; transform: translateX(-1px); }
      `}} />

      <div className="heritage-card overflow-hidden border border-border bg-[#f8f6f0] shadow-inner relative flex flex-col" style={{ height: "65vh" }}>
        
        <div className="bg-white/80 backdrop-blur border-b border-border p-3 px-5 flex justify-between items-center z-10 sticky top-0">
          <div>
            <h3 className="font-serif font-bold text-lg flex items-center gap-2">
              Interactive Heritage Tree
              {actionLoading && <Loader2 className="w-4 h-4 animate-spin text-accent" />}
            </h3>
            <p className="text-xs text-text-muted">Drag a profile onto another to link them. Hover for quick actions (Unlink/Delete).</p>
          </div>
          <button 
            type="button"
            onClick={handleAddMember}
            className="flex items-center gap-2 px-4 py-2 bg-accent text-white rounded-xl shadow-sm hover:shadow-md transition-all text-sm font-semibold active:scale-95"
          >
            <UserPlus className="w-4 h-4" />
            Add New Member
          </button>
        </div>

        <div className="flex-1 overflow-auto w-full relative custom-scrollbar p-12">
          {loading ? (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="w-8 h-8 animate-spin text-accent" />
            </div>
          ) : (
            <div className="family-tree min-w-max pb-20">
              <ul>
                {tree.map((root) => (
                  <AdminFamilyTreeNode
                    key={root.id}
                    member={root}
                    onDelete={handleDeleteMember}
                    onUnlink={handleUnlinkMember}
                    onAddChild={(m) => {
                      setEditingMember({ id: "new", FullName: "" });
                      const updates: any = {
                        FullName: "",
                        Gender: "Male",
                        IsAlive: true,
                        ProfileImageUrl: "",
                        Generation: (m.Generation || 1) + 1,
                      };
                      if (m.Gender === "Male") {
                        updates.FatherRecordId = m.id;
                        if (m.Spouse) updates.MotherRecordId = m.Spouse.id;
                      } else {
                        updates.MotherRecordId = m.id;
                        if (m.Spouse) updates.FatherRecordId = m.Spouse.id;
                      }
                      setEditForm(updates);
                    }}
                    onNodeClick={(m) => {
                      setEditingMember(m);
                      setEditForm({
                        FullName: m.FullName,
                        FatherName: m.FatherName,
                        MotherName: m.MotherName,
                        SpouseName: m.SpouseName,
                        DateOfBirth: m.DateOfBirth,
                        DateOfDeath: m.DateOfDeath,
                        Gender: m.Gender,
                        IsAlive: m.IsAlive ?? true,
                        ProfileImageUrl: m.ProfileImageUrl,
                        CurrentCity: m.CurrentCity,
                        CurrentCountry: m.CurrentCountry,
                        Email: m.Email,
                        PhoneNumber: m.PhoneNumber,
                        Biography: m.Biography,
                        FatherRecordId: m.FatherRecordId,
                        MotherRecordId: m.MotherRecordId,
                      });
                    }}
                    onDropNode={handleDropNode}
                  />
                ))}
              </ul>
            </div>
          )}
        </div>
      </div>

      {editingMember && (
        <div className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 backdrop-blur-sm p-4 animate-fadeIn">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[90vh] flex flex-col overflow-hidden animate-fadeInUp">
            <div className="flex items-center justify-between p-4 border-b border-border bg-bg-secondary sticky top-0 shrink-0">
              <h3 className="font-serif font-bold text-lg text-text-primary">Edit Member Info</h3>
              <button onClick={() => setEditingMember(null)} className="p-1 text-text-muted hover:text-text-primary">
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <div className="p-6 overflow-y-auto space-y-5 custom-scrollbar bg-bg-primary">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="p-4 bg-sky/5 rounded-xl border border-sky/20">
                  <label className="text-xs font-bold text-sky uppercase tracking-wide mb-1.5 block">Structural Father (Connection Line)</label>
                  <select value={editForm.FatherRecordId || ""} onChange={(e) => setEditForm({...editForm, FatherRecordId: e.target.value})} className="input-heritage w-full bg-white relative z-50">
                    <option value="">-- No Father (Root Level) --</option>
                    {flattenTree(tree).filter(m => m.id !== editingMember.id && m.Gender === "Male").map(m => (
                      <option key={m.id} value={m.id}>{m.FullName}</option>
                    ))}
                    {flattenTree(tree).filter(m => m.id !== editingMember.id && m.Gender !== "Male").map(m => (
                      <option key={m.id} value={m.id}>{m.FullName} (F)</option>
                    ))}
                  </select>
                </div>

                <div className="p-4 bg-plum/5 rounded-xl border border-plum/20">
                  <label className="text-xs font-bold text-plum uppercase tracking-wide mb-1.5 block">Structural Mother (Connection Line)</label>
                  <select value={editForm.MotherRecordId || ""} onChange={(e) => setEditForm({...editForm, MotherRecordId: e.target.value})} className="input-heritage w-full bg-white relative z-50">
                    <option value="">-- No Mother (Root Level) --</option>
                    {flattenTree(tree).filter(m => m.id !== editingMember.id && m.Gender === "Female").map(m => (
                      <option key={m.id} value={m.id}>{m.FullName}</option>
                    ))}
                    {flattenTree(tree).filter(m => m.id !== editingMember.id && m.Gender !== "Female").map(m => (
                      <option key={m.id} value={m.id}>{m.FullName} (M)</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                <div>
                  <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Full Name</label>
                  <input type="text" value={editForm.FullName || ""} onChange={(e) => setEditForm({ ...editForm, FullName: e.target.value })} className="input-heritage w-full" />
                </div>
                <div>
                  <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Gender</label>
                  <select value={editForm.Gender || "Male"} onChange={(e) => setEditForm({...editForm, Gender: e.target.value})} className="input-heritage w-full">
                    <option value="Male">Male</option>
                    <option value="Female">Female</option>
                  </select>
                </div>

                <div>
                  <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Father Name</label>
                  <input type="text" value={editForm.FatherName || ""} onChange={(e) => setEditForm({ ...editForm, FatherName: e.target.value })} className="input-heritage w-full" />
                </div>
                <div>
                  <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Mother Name</label>
                  <input type="text" value={editForm.MotherName || ""} onChange={(e) => setEditForm({ ...editForm, MotherName: e.target.value })} className="input-heritage w-full" />
                </div>

                <div>
                  <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Date of Birth</label>
                  <input type="text" value={editForm.DateOfBirth || ""} onChange={(e) => setEditForm({ ...editForm, DateOfBirth: e.target.value })} className="input-heritage w-full" />
                </div>
                <div>
                  <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Date of Death</label>
                  <input type="text" value={editForm.DateOfDeath || ""} onChange={(e) => setEditForm({ ...editForm, DateOfDeath: e.target.value })} disabled={editForm.IsAlive} className="input-heritage w-full disabled:opacity-50" />
                </div>
              </div>

              <div className="flex items-center gap-2 mb-2 p-3 bg-bg-secondary rounded-xl border border-border">
                <input type="checkbox" id="isalive" checked={editForm.IsAlive || false} onChange={e => setEditForm({...editForm, IsAlive: e.target.checked})} className="w-5 h-5 rounded border-gray-300 text-accent focus:ring-accent" />
                <label htmlFor="isalive" className="text-sm font-medium">This person is currently alive</label>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-5">
                <div>
                  <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Email</label>
                  <input type="email" value={editForm.Email || ""} onChange={(e) => setEditForm({ ...editForm, Email: e.target.value })} className="input-heritage w-full" />
                </div>
                <div>
                  <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Phone Number</label>
                  <input type="tel" value={editForm.PhoneNumber || ""} onChange={(e) => setEditForm({ ...editForm, PhoneNumber: e.target.value })} className="input-heritage w-full" />
                </div>
                <div className="col-span-1 sm:col-span-2">
                  <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Profile Image</label>
                  {editForm.ProfileImageUrl ? (
                    <div className="flex items-center gap-4 p-3 border border-border rounded-lg bg-bg-primary">
                      <img src={editForm.ProfileImageUrl} alt="Profile preview" className="w-12 h-12 rounded-full object-cover" />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-text-primary truncate">Image Uploaded</p>
                        <button type="button" onClick={() => setEditForm({ ...editForm, ProfileImageUrl: "" })} className="text-xs text-terracotta hover:underline">Remove</button>
                      </div>
                    </div>
                  ) : (
                    <div className="relative">
                      <input type="file" accept="image/*" onChange={handlePhotoUpload} disabled={uploadingImage} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed" />
                      <div className={`w-full px-4 py-2.5 rounded-lg border border-border bg-bg-primary flex items-center justify-center gap-2 transition-all ${uploadingImage ? 'opacity-50' : 'hover:border-accent hover:text-accent'}`}>
                        {uploadingImage ? <Loader2 className="w-4 h-4 animate-spin text-accent" /> : <Edit3 className="w-4 h-4 text-text-muted" />}
                        <span className="text-sm font-medium text-text-muted">{uploadingImage ? "Uploading..." : "Click to select a photo"}</span>
                      </div>
                    </div>
                  )}
                </div>
                <div className="col-span-1 sm:col-span-2">
                  <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block">Biography</label>
                  <textarea rows={4} value={editForm.Biography || ""} onChange={(e) => setEditForm({ ...editForm, Biography: e.target.value })} className="input-heritage w-full resize-y" />
                </div>
              </div>
            </div>

            <div className="p-4 border-t border-border bg-bg-secondary flex justify-end gap-3 sticky bottom-0 shrink-0">
              <button 
                type="button"
                onClick={() => setEditingMember(null)} 
                className="px-5 py-2.5 text-sm font-semibold text-text-muted hover:text-text-primary rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button 
                onClick={editingMember.id === "new" ? handleCreateNew : handleSaveEdit}
                disabled={actionLoading}
                className="btn-primary"
              >
                {actionLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                {editingMember.id === "new" ? "Create Member" : "Save Changes"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

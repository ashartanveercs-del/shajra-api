"use client";

import { useEffect, useState } from "react";
import { fetchTree, adminUpdateMember, uploadImage, type Member } from "@/lib/api";
import { Heart, Loader2, X, Save, GripHorizontal, Edit3 } from "lucide-react";

function AdminTreeCard({
  member,
  onNodeClick,
  onDropNode,
}: {
  member: Member;
  onNodeClick: (m: Member) => void;
  onDropNode: (draggedId: string, targetId: string) => void;
}) {
  const genderBg =
    member.Gender === "Male"
      ? "bg-sky-light/80 border-sky/30 text-sky-900"
      : member.Gender === "Female"
      ? "bg-plum-light/80 border-plum/30 text-plum-900"
      : "bg-bg-secondary border-border text-text-primary";

  const handleDragStart = (e: React.DragEvent) => {
    e.dataTransfer.setData("text/plain", member.id);
    e.dataTransfer.effectAllowed = "move";
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    const draggedId = e.dataTransfer.getData("text/plain");
    if (draggedId && draggedId !== member.id) {
      onDropNode(draggedId, member.id);
    }
  };

  return (
    <div className="group inline-block">
      <div
        draggable
        onDragStart={handleDragStart}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        onClick={(e) => {
          e.preventDefault();
          onNodeClick(member);
        }}
        className={`flex flex-col items-center p-3 rounded-2xl border ${genderBg} shadow-sm hover:shadow-md transition-all min-w-[140px] relative cursor-grab active:cursor-grabbing ring-2 ring-transparent hover:ring-accent/50 group-hover:-translate-y-1`}
      >
        <div className="absolute top-1.5 right-1.5 opacity-0 group-hover:opacity-100 transition-opacity p-1 bg-white/70 rounded-md text-text-primary shadow-sm pointer-events-none">
          <Edit3 className="w-3 h-3" />
        </div>
        <div
          className="w-12 h-12 rounded-full bg-white/70 flex items-center justify-center text-lg font-serif font-bold mb-2 bg-cover bg-center relative z-10"
          style={member.ProfileImageUrl ? { backgroundImage: `url(${member.ProfileImageUrl})` } : {}}
        >
          {!member.ProfileImageUrl && (member.FullName || "?")[0]}
          {member.IsAlive && (
            <div className="absolute -top-1 -right-1 z-20 w-4 h-4 rounded-full bg-white flex items-center justify-center border border-emerald/20 shadow-sm">
              <Heart className="w-2.5 h-2.5 text-emerald fill-emerald" />
            </div>
          )}
        </div>
        <div className="font-semibold text-sm leading-snug w-full truncate px-1">
          {member.FullName || "Unknown"}
        </div>
        {(member.DateOfBirth || member.DateOfDeath) && (
          <div className="text-[10px] opacity-70 mt-0.5">
            {member.DateOfBirth || "?"} &mdash; {member.DateOfDeath || "Present"}
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
}: {
  member: Member;
  onNodeClick: (m: Member) => void;
  onDropNode: (draggedId: string, targetId: string) => void;
}) {
  return (
    <li>
      <AdminTreeCard
        member={member}
        onNodeClick={onNodeClick}
        onDropNode={onDropNode}
      />
      {member.children && member.children.length > 0 && (
        <ul>
          {member.children.map((child) => (
            <AdminFamilyTreeNode
              key={child.id}
              member={child}
              onNodeClick={onNodeClick}
              onDropNode={onDropNode}
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
      list.push(n);
      if(n.children) list = list.concat(flattenTree(n.children));
    });
    return list;
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
      await adminUpdateMember(token, draggedId, { FatherRecordId: targetId });
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

  return (
    <div className="relative">
      <style dangerouslySetInnerHTML={{
        __html: `
        .family-tree { display: block; padding-bottom: 3rem; }
        .family-tree > ul { padding-top: 0; display: flex; justify-content: center; width: max-content; margin: 0 auto; }
        .family-tree ul { padding-top: 30px; position: relative; transition: all 0.5s; display: flex; justify-content: center; }
        .family-tree li { float: left; text-align: center; list-style-type: none; position: relative; padding: 30px 10px 0 10px; transition: all 0.5s; }
        .family-tree li::before, .family-tree li::after { content: ''; position: absolute; top: 0; right: 50%; border-top: 2px solid #cbd5e1; width: 50%; height: 30px; }
        .family-tree li::after { right: auto; left: 50%; border-left: 2px solid #cbd5e1; }
        .family-tree li:only-child::after, .family-tree li:only-child::before { display: none; }
        .family-tree li:only-child { padding-top: 0; }
        .family-tree li:first-child::before, .family-tree li:last-child::after { border: 0 none; }
        .family-tree li:last-child::before { border-right: 2px solid #cbd5e1; border-radius: 0 5px 0 0; }
        .family-tree li:first-child::after { border-radius: 5px 0 0 0; }
        .family-tree ul ul::before { content: ''; position: absolute; top: 0; left: 50%; border-left: 2px solid #cbd5e1; width: 0; height: 30px; transform: translateX(-1px); }
      `}} />

      <div className="heritage-card overflow-hidden border border-border bg-[#f8f6f0] shadow-inner relative flex flex-col" style={{ height: "65vh" }}>
        
        <div className="bg-white/80 backdrop-blur border-b border-border p-3 px-5 flex justify-between items-center z-10 sticky top-0">
          <div>
            <h3 className="font-serif font-bold text-lg flex items-center gap-2">
              Drag & Drop Tree Editor
              {actionLoading && <Loader2 className="w-4 h-4 animate-spin text-accent" />}
            </h3>
            <p className="text-xs text-text-muted">Drag a profile onto another to link them as their child. Click a profile to edit all details.</p>
          </div>
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
              <div className="col-span-1 sm:col-span-2 p-4 bg-terracotta/5 rounded-xl border border-terracotta/20">
                <label className="text-xs font-bold text-terracotta uppercase tracking-wide mb-1.5 block">Structural Parent (Connection Line)</label>
                <p className="text-[10.5px] text-text-muted mb-2 leading-relaxed">Changes who this person is connected to on the tree.</p>
                <select value={editForm.FatherRecordId || ""} onChange={(e) => setEditForm({...editForm, FatherRecordId: e.target.value})} className="input-heritage w-full bg-white relative z-50">
                  <option value="">-- No Parent (Root Level) --</option>
                  {flattenTree(tree).filter(m => m.id !== editingMember.id).map(m => (
                    <option key={m.id} value={m.id}>{m.FullName}</option>
                  ))}
                </select>
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
              <button onClick={() => setEditingMember(null)} className="px-5 py-2.5 text-sm font-semibold text-text-muted hover:text-text-primary rounded-lg transition-colors">Cancel</button>
              <button 
                onClick={handleSaveEdit}
                disabled={actionLoading}
                className="btn-primary"
              >
                {actionLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Save className="w-4 h-4" />}
                Save Changes
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

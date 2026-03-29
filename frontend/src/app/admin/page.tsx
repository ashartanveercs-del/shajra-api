"use client";

import AdminTreeEditor from "@/components/AdminTreeEditor";

import { useState, useEffect, useCallback } from "react";
import {
  adminLogin,
  fetchPending,
  approveSubmission,
  rejectSubmission,
  fetchMembers,
  adminCreateMember,
  adminDeleteMember,
  adminFetchApprovedEmails,
  adminAddApprovedEmail,
  adminDeleteApprovedEmail,
  adminFetchSettings,
  adminUpdateSettings,
  adminUndo,
  adminHeal,
  type PendingSubmission,
  type Member,
  type ApprovedEmail,
} from "@/lib/api";
import {
  ShieldCheck,
  LogIn,
  LogOut,
  Check,
  X,
  AlertTriangle,
  Loader2,
  Plus,
  Trash2,
  Users,
  ClipboardList,
  Brain,
  Eye,
  Mail,
  Settings,
  Network,
  Undo2,
  HeartPulse
} from "lucide-react";

type Tab = "pending" | "members" | "tree" | "add" | "emails" | "settings";

export default function AdminPage() {
  const [token, setToken] = useState<string | null>(null);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [tab, setTab] = useState<Tab>("pending");
  const [pending, setPending] = useState<PendingSubmission[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [emails, setEmails] = useState<ApprovedEmail[]>([]);
  const [loading, setLoading] = useState(false);
  const [actionLoading, setActionLoading] = useState<string | null>(null);

  useEffect(() => {
    const saved = localStorage.getItem("shajra_admin_token");
    if (saved) setToken(saved);
  }, []);

  const handleLogin = async () => {
    setLoginLoading(true);
    setLoginError("");
    try {
      const t = await adminLogin(username, password);
      setToken(t);
      localStorage.setItem("shajra_admin_token", t);
    } catch {
      setLoginError("Invalid username or password");
    }
    setLoginLoading(false);
  };

  const handleLogout = () => {
    setToken(null);
    localStorage.removeItem("shajra_admin_token");
  };

  const loadData = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    try {
      const [p, m, e] = await Promise.all([fetchPending(token), fetchMembers(), adminFetchApprovedEmails(token)]);
      setPending(p);
      setMembers(m);
      setEmails(e);
    } catch { /* token expired */ }
    setLoading(false);
  }, [token]);

  useEffect(() => { loadData(); }, [loadData]);

  const handleApprove = async (id: string) => {
    if (!token) return;
    setActionLoading(id);
    try { await approveSubmission(token, id); await loadData(); }
    catch (e) { alert("Failed: " + (e as Error).message); }
    setActionLoading(null);
  };

  const handleReject = async (id: string) => {
    if (!token) return;
    setActionLoading(id);
    try { await rejectSubmission(token, id); await loadData(); }
    catch (e) { alert("Failed: " + (e as Error).message); }
    setActionLoading(null);
  };

  const handleDelete = async (id: string) => {
    if (!token || !confirm("Delete this member permanently?")) return;
    setActionLoading(id);
    try { await adminDeleteMember(token, id); await loadData(); }
    catch (e) { alert("Failed: " + (e as Error).message); }
    setActionLoading(null);
  };

  // ── Login ──
  if (!token) {
    return (
      <div className="min-h-[80vh] flex items-center justify-center px-5">
        <div className="heritage-card p-8 sm:p-10 w-full max-w-md animate-fadeInUp">
          <div className="text-center mb-8">
            <div className="w-14 h-14 mx-auto mb-4 rounded-2xl bg-accent flex items-center justify-center">
              <ShieldCheck className="w-7 h-7 text-white" />
            </div>
            <h1 className="heading-serif text-2xl font-bold">Admin Portal</h1>
            <p className="text-text-muted text-sm mt-1">Sign in to manage family records</p>
          </div>

          <div className="space-y-4">
            <div>
              <label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Username</label>
              <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleLogin()} className="input-heritage" placeholder="admin" />
            </div>
            <div>
              <label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Password</label>
              <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleLogin()} className="input-heritage" placeholder="Enter password" />
            </div>
            {loginError && (
              <div className="flex items-center gap-2 text-terracotta text-sm bg-terracotta-light p-3 rounded-lg">
                <AlertTriangle className="w-4 h-4" />
                {loginError}
              </div>
            )}
            <button onClick={handleLogin} disabled={loginLoading} className="btn-primary w-full justify-center py-3">
              {loginLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogIn className="w-4 h-4" />}
              Sign In
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Dashboard ──
  const pendingOnly = pending.filter((p) => p.Status === "Pending");

  return (
    <div className="mx-auto max-w-5xl px-5 sm:px-8 py-10 sm:py-14">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="heading-serif text-2xl sm:text-3xl font-bold">Admin Dashboard</h1>
          <p className="text-text-muted text-sm mt-1">Manage submissions, members, and access</p>
        </div>
        <button onClick={handleLogout} className="flex items-center gap-1.5 px-3 py-2 text-sm text-text-muted hover:text-terracotta hover:bg-terracotta-light rounded-lg transition-heritage">
          <LogOut className="w-4 h-4" />
          Logout
        </button>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1.5 mb-7 border-b border-border pb-4 overflow-x-auto rendering-wrapper">
        {[
          { key: "pending", label: "Pending", icon: ClipboardList, badge: pendingOnly.length },
          { key: "members", label: "Members", icon: Users, badge: members.length },
          { key: "tree", label: "Tree Editor", icon: Network },
          { key: "add", label: "Add Member", icon: Plus },
          { key: "emails", label: "Approved Emails", icon: Mail, badge: emails.length },
          { key: "settings", label: "Settings", icon: Settings },
        ].map((t) => (
          <button
            key={t.key}
            onClick={() => setTab(t.key as Tab)}
            className={`flex items-center gap-1.5 px-4 py-2 rounded-lg text-[13px] font-medium transition-heritage whitespace-nowrap ${
              tab === t.key
                ? "bg-accent/8 text-accent"
                : "text-text-muted hover:text-text-primary hover:bg-bg-secondary"
            }`}
          >
            <t.icon className="w-3.5 h-3.5" />
            {t.label}
            {t.badge !== undefined && t.badge > 0 && (
              <span className="px-1.5 py-0.5 text-[11px] rounded-full bg-accent/10 text-accent font-semibold">{t.badge}</span>
            )}
          </button>
        ))}
      <button onClick={loadData} disabled={loading} className="ml-auto text-xs text-text-light hover:text-accent transition-heritage flex-shrink-0 pl-4">
          {loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "Refresh"}
        </button>
      </div>

      {/* Quick Actions Bar */}
      <div className="flex items-center gap-2 mb-5 flex-wrap">
        <button
          onClick={async () => {
            try {
              const result = await adminUndo(token!);
              alert(`✅ ${result.action || "Undone"}`);
              loadData();
            } catch (e: any) { alert(e.message); }
          }}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-text-muted hover:text-accent hover:bg-accent/5 border border-border rounded-lg transition-heritage"
        >
          <Undo2 className="w-3.5 h-3.5" />
          Undo Last Change
        </button>
        <button
          onClick={async () => {
            try {
              const result = await adminHeal(token!);
              if (result.fixes_applied === 0) {
                alert("✅ Graph is healthy — no fixes needed.");
              } else {
                alert(`🔧 Applied ${result.fixes_applied} fix(es):\n\n${result.details.join("\n")}`);
                loadData();
              }
            } catch (e: any) { alert(e.message); }
          }}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-emerald hover:bg-emerald/5 border border-emerald/20 rounded-lg transition-heritage"
        >
          <HeartPulse className="w-3.5 h-3.5" />
          Heal Graph
        </button>
      </div>

      {tab === "pending" && <PendingTab submissions={pendingOnly} actionLoading={actionLoading} onApprove={handleApprove} onReject={handleReject} />}
      {tab === "members" && <MembersTab members={members} actionLoading={actionLoading} onDelete={handleDelete} />}
      {tab === "tree" && <AdminTreeEditor token={token!} onUpdated={loadData} />}
      {tab === "add" && <AddMemberTab token={token} onCreated={loadData} members={members} />}
      {tab === "emails" && <EmailsTab emails={emails} token={token} onUpdated={loadData} />}
      {tab === "settings" && <SettingsTab token={token} />}
    </div>
  );
}

function EmailsTab({ emails, token, onUpdated }: { emails: ApprovedEmail[]; token: string; onUpdated: () => void }) {
  const [newEmail, setNewEmail] = useState("");
  const [newName, setNewName] = useState("");
  const [loadingId, setLoadingId] = useState<string|null>(null);

  const handleAdd = async () => {
    if (!newEmail) return;
    setLoadingId("add");
    try {
      await adminAddApprovedEmail(token, newEmail, newName);
      setNewEmail("");
      setNewName("");
      onUpdated();
    } catch (e: any) { alert(e.message); }
    setLoadingId(null);
  };

  const handleRemove = async (id: string) => {
    if (!confirm("Remove this email?")) return;
    setLoadingId(id);
    try {
      await adminDeleteApprovedEmail(token, id);
      onUpdated();
    } catch (e: any) { alert(e.message); }
    setLoadingId(null);
  };

  return (
    <div className="space-y-6">
      <div className="heritage-card p-6 py-8">
        <h2 className="font-serif text-lg font-semibold mb-2">Authorize Family Members</h2>
        <p className="text-text-muted text-sm mb-6 max-w-2xl">
          To prevent spam and protect privacy, only users whose email is on this list can leave comments or post stories on profiles.
        </p>

        <div className="flex flex-col sm:flex-row gap-3">
          <input type="text" placeholder="Name (optional)" value={newName} onChange={e=>setNewName(e.target.value)} className="input-heritage sm:w-1/3" />
          <input type="email" placeholder="name@example.com *" value={newEmail} onChange={e=>setNewEmail(e.target.value)} className="input-heritage flex-1" />
          <button onClick={handleAdd} disabled={!newEmail || loadingId === "add"} className="btn-primary whitespace-nowrap">
            {loadingId === "add" ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />} Add Email
          </button>
        </div>
      </div>

      <div className="heritage-card overflow-hidden">
        <table className="w-full text-left text-sm">
          <thead className="bg-bg-secondary text-text-light text-xs uppercase tracking-wider">
            <tr>
              <th className="px-6 py-4 font-medium">Email</th>
              <th className="px-6 py-4 font-medium">Name</th>
              <th className="px-6 py-4 font-medium text-right">Action</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {emails.length === 0 ? (
              <tr><td colSpan={3} className="px-6 py-8 text-center text-text-light italic">No verified emails yet.</td></tr>
            ) : (
              emails.map((e) => (
                <tr key={e.id} className="hover:bg-bg-card/50 transition-colors">
                  <td className="px-6 py-4 font-medium text-text-primary">{e.Email}</td>
                  <td className="px-6 py-4 text-text-muted">{e.Name || "—"}</td>
                  <td className="px-6 py-4 text-right">
                    <button onClick={() => handleRemove(e.id)} disabled={loadingId === e.id} className="text-text-light hover:text-terracotta p-2 rounded-lg hover:bg-terracotta/10 transition-colors">
                      {loadingId === e.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
                    </button>
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function PendingTab({ submissions, actionLoading, onApprove, onReject }: { submissions: PendingSubmission[]; actionLoading: string | null; onApprove: (id: string) => void; onReject: (id: string) => void }) {
  const [expanded, setExpanded] = useState<string | null>(null);

  if (submissions.length === 0) {
    return (
      <div className="heritage-card p-14 text-center">
        <Check className="w-10 h-10 mx-auto mb-4 text-emerald" />
        <h2 className="font-serif text-xl font-semibold mb-2">All caught up!</h2>
        <p className="text-text-muted text-sm">No pending submissions to review.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {submissions.map((sub) => (
        <div key={sub.id} className="heritage-card p-6 animate-fadeInUp">
          <div className="flex items-start justify-between gap-4">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2.5 mb-2 flex-wrap">
                <h3 className="font-serif font-semibold text-lg text-text-primary">{sub.CleanFullName || sub.RawFullName || "Unknown"}</h3>
                {sub.AIDuplicateFlag && (
                  <span className="flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-full bg-terracotta-light text-terracotta font-medium">
                    <AlertTriangle className="w-3 h-3" /> Duplicate?
                  </span>
                )}
                {sub.AIConfidence !== undefined && (
                  <span className="flex items-center gap-1 px-2 py-0.5 text-[11px] rounded-full bg-sky-light text-sky font-medium">
                    <Brain className="w-3 h-3" /> {Math.round((sub.AIConfidence || 0) * 100)}%
                  </span>
                )}
              </div>
              <div className="grid grid-cols-2 sm:grid-cols-3 gap-x-4 gap-y-1 text-sm text-text-secondary">
                {sub.CleanFatherName && <div><span className="text-text-light">Father:</span> {sub.CleanFatherName}</div>}
                {sub.CleanDOB && <div><span className="text-text-light">DOB:</span> {sub.CleanDOB}</div>}
                {sub.CleanCity && <div><span className="text-text-light">City:</span> {sub.CleanCity}</div>}
                {sub.CleanGender && <div><span className="text-text-light">Gender:</span> {sub.CleanGender}</div>}
              </div>
              {sub.AINotes && (
                <div className="mt-3 px-3 py-2 rounded-lg bg-sky-light text-xs text-sky font-medium">
                  <Brain className="w-3 h-3 inline mr-1" /> AI Note: {sub.AINotes}
                </div>
              )}
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <button onClick={() => setExpanded(expanded === sub.id ? null : sub.id)} className="p-2 rounded-lg text-text-light hover:text-text-primary hover:bg-bg-secondary transition-heritage" title="View details">
                <Eye className="w-4 h-4" />
              </button>
              <button onClick={() => onApprove(sub.id)} disabled={actionLoading === sub.id} className="flex items-center gap-1 px-3 py-2 bg-emerald-light text-emerald rounded-lg text-xs font-medium hover:bg-emerald hover:text-white transition-heritage disabled:opacity-50">
                {actionLoading === sub.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />} Approve
              </button>
              <button onClick={() => onReject(sub.id)} disabled={actionLoading === sub.id} className="flex items-center gap-1 px-3 py-2 bg-terracotta-light text-terracotta rounded-lg text-xs font-medium hover:bg-terracotta hover:text-white transition-heritage disabled:opacity-50">
                <X className="w-3.5 h-3.5" /> Reject
              </button>
            </div>
          </div>

          {expanded === sub.id && (
            <div className="mt-4 pt-4 border-t border-border grid md:grid-cols-2 gap-4 text-xs animate-fadeInUp">
              <div>
                <h4 className="font-semibold text-text-light mb-2 uppercase tracking-wide">Raw (User Submitted)</h4>
                <pre className="bg-bg-secondary p-3 rounded-lg overflow-x-auto text-text-secondary font-mono">{JSON.stringify({ Name: sub.RawFullName, Father: sub.RawFatherName, Spouse: sub.RawSpouseName, DOB: sub.RawDateOfBirth, DOD: sub.RawDateOfDeath, Location: sub.RawLocation, Burial: sub.RawBurialLocation, Gender: sub.RawGender, Bio: sub.RawBiography }, null, 2)}</pre>
              </div>
              <div>
                <h4 className="font-semibold text-accent mb-2 uppercase tracking-wide">AI Structured Data</h4>
                <pre className="bg-sky-light/30 border border-sky/10 p-3 rounded-lg overflow-x-auto text-sky-900 font-mono">{JSON.stringify({ Name: sub.CleanFullName, Father: sub.CleanFatherName, Spouse: sub.CleanSpouseName, DOB: sub.CleanDOB, DOD: sub.CleanDOD, City: sub.CleanCity, Country: sub.CleanCountry, Burial: sub.CleanBurialLocation, Gender: sub.CleanGender, AIMatchedFatherId: sub.AIMatchedFatherId, AIMatchedSpouseId: sub.AIMatchedSpouseId }, null, 2)}</pre>
              </div>
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

function MembersTab({ members, actionLoading, onDelete }: { members: Member[]; actionLoading: string | null; onDelete: (id: string) => void }) {
  if (members.length === 0) {
    return (
      <div className="heritage-card p-14 text-center">
        <Users className="w-10 h-10 mx-auto mb-4 text-text-light" />
        <h2 className="font-serif text-xl font-semibold mb-2">No members yet</h2>
        <p className="text-text-muted text-sm">Add members or approve submissions.</p>
      </div>
    );
  }
  return (
    <div className="space-y-2">
      {members.map((m) => (
        <div key={m.id} className="heritage-card p-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 rounded-lg bg-accent/8 flex items-center justify-center font-serif font-bold text-accent flex-shrink-0 text-sm">
              {(m.FullName || "?")[0]}
            </div>
            <div className="min-w-0">
              <div className="font-medium text-sm text-text-primary truncate">{m.FullName}</div>
              <div className="text-xs text-text-muted truncate">{m.CurrentCity || ""}{m.CurrentCountry ? ` | ${m.CurrentCountry}` : ""}{m.Generation ? ` | Gen ${m.Generation}` : ""}</div>
            </div>
          </div>
          <button onClick={() => onDelete(m.id)} disabled={actionLoading === m.id} className="p-2 text-text-light hover:text-terracotta hover:bg-terracotta-light rounded-lg transition-heritage">
            {actionLoading === m.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
          </button>
        </div>
      ))}
    </div>
  );
}

function AddMemberTab({ token, onCreated, members }: { token: string; onCreated: () => void; members: Member[] }) {
  const [form, setForm] = useState({
    FullName: "", FatherName: "", MotherName: "", SpouseName: "", DateOfBirth: "", DateOfDeath: "",
    CurrentCity: "", CurrentCountry: "", BurialLocation: "", Biography: "", Gender: "",
    Generation: "", Branch: "", FatherRecordId: "", MotherRecordId: "", SpouseRecordId: "", IsAlive: true,
  });
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);

  const handleSubmit = async () => {
    if (!form.FullName.trim()) return alert("Full Name is required");
    setSaving(true);
    try {
      const fields: Record<string, unknown> = { ...form };
      if (form.Generation) fields.Generation = parseInt(form.Generation);
      else delete fields.Generation;
      Object.keys(fields).forEach((k) => { if (fields[k] === "") delete fields[k]; });
      await adminCreateMember(token, fields as Partial<Member>);
      setSuccess(true);
      setForm({ FullName: "", FatherName: "", MotherName: "", SpouseName: "", DateOfBirth: "", DateOfDeath: "", CurrentCity: "", CurrentCountry: "", BurialLocation: "", Biography: "", Gender: "", Generation: "", Branch: "", FatherRecordId: "", MotherRecordId: "", SpouseRecordId: "", IsAlive: true });
      onCreated();
      setTimeout(() => setSuccess(false), 3000);
    } catch (e) { alert("Failed: " + (e as Error).message); }
    setSaving(false);
  };

  return (
    <div className="heritage-card p-7 max-w-3xl animate-fadeInUp">
      <h2 className="font-serif text-xl font-semibold mb-6 text-text-primary">Add New Member</h2>

      {success && (
        <div className="mb-6 flex items-center gap-2 px-4 py-3 rounded-lg bg-emerald-light text-emerald text-sm font-medium">
          <Check className="w-4 h-4" /> Member added successfully!
        </div>
      )}

      <div className="grid sm:grid-cols-2 gap-4">
        <div><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Full Name *</label><input type="text" value={form.FullName} onChange={(e) => setForm({ ...form, FullName: e.target.value })} className="input-heritage" placeholder="Muhammad Ali Khan" /></div>
        <div><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Gender</label><select value={form.Gender} onChange={(e) => setForm({ ...form, Gender: e.target.value })} className="input-heritage"><option value="">Select</option><option value="Male">Male</option><option value="Female">Female</option><option value="Other">Other</option></select></div>
        <div><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Father Name</label><input type="text" value={form.FatherName} onChange={(e) => setForm({ ...form, FatherName: e.target.value })} className="input-heritage" placeholder="Father's name" /></div>
        <div><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Link to Father</label><select value={form.FatherRecordId} onChange={(e) => setForm({ ...form, FatherRecordId: e.target.value })} className="input-heritage"><option value="">-- Select --</option>{members.filter(m => m.Gender !== "Female").map((m) => (<option key={m.id} value={m.id}>{m.FullName}</option>))}</select></div>
        <div><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Mother Name</label><input type="text" value={form.MotherName} onChange={(e) => setForm({ ...form, MotherName: e.target.value })} className="input-heritage" placeholder="Mother's name" /></div>
        <div><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Spouse Name</label><input type="text" value={form.SpouseName} onChange={(e) => setForm({ ...form, SpouseName: e.target.value })} className="input-heritage" placeholder="Spouse's name" /></div>
        <div><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Date of Birth</label><input type="text" value={form.DateOfBirth} onChange={(e) => setForm({ ...form, DateOfBirth: e.target.value })} className="input-heritage" placeholder="YYYY-MM-DD" /></div>
        <div><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Date of Death</label><input type="text" value={form.DateOfDeath} onChange={(e) => setForm({ ...form, DateOfDeath: e.target.value })} className="input-heritage" placeholder="Leave empty if alive" /></div>
        <div><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">City</label><input type="text" value={form.CurrentCity} onChange={(e) => setForm({ ...form, CurrentCity: e.target.value })} className="input-heritage" placeholder="Karachi" /></div>
        <div><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Country</label><input type="text" value={form.CurrentCountry} onChange={(e) => setForm({ ...form, CurrentCountry: e.target.value })} className="input-heritage" placeholder="Pakistan" /></div>
        <div><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Generation</label><input type="number" value={form.Generation} onChange={(e) => setForm({ ...form, Generation: e.target.value })} className="input-heritage" placeholder="1, 2, 3..." /></div>
        <div><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Branch</label><input type="text" value={form.Branch} onChange={(e) => setForm({ ...form, Branch: e.target.value })} className="input-heritage" placeholder="Family branch" /></div>
        <div><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Burial Location</label><input type="text" value={form.BurialLocation} onChange={(e) => setForm({ ...form, BurialLocation: e.target.value })} className="input-heritage" placeholder="Cemetery / City" /></div>
        <div className="flex items-end pb-1"><label className="flex items-center gap-2 text-sm cursor-pointer"><input type="checkbox" checked={form.IsAlive} onChange={(e) => setForm({ ...form, IsAlive: e.target.checked })} className="w-4 h-4 accent-[#8b6f47]" /> Is Alive</label></div>
      </div>

      <div className="mt-4"><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Biography</label><textarea value={form.Biography} onChange={(e) => setForm({ ...form, Biography: e.target.value })} rows={3} className="input-heritage" placeholder="Notes about this family member..." /></div>

      <button onClick={handleSubmit} disabled={saving} className="btn-primary mt-6">
        {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
        Add Member
      </button>
    </div>
  );
}

function SettingsTab({ token }: { token: string }) {
  const [groqKey, setGroqKey] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    setLoading(true);
    adminFetchSettings(token)
      .then((data) => setGroqKey(data.GROQ_API_KEY || ""))
      .catch((e) => console.error("Could not fetch settings", e))
      .finally(() => setLoading(false));
  }, [token]);

  const handleSave = async () => {
    setSaving(true);
    setSuccess(false);
    try {
      await adminUpdateSettings(token, { GROQ_API_KEY: groqKey });
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (e: any) {
      alert("Failed to save settings: " + e.message);
    }
    setSaving(false);
  };

  return (
    <div className="heritage-card p-7 max-w-2xl animate-fadeInUp">
      <h2 className="font-serif text-xl font-semibold mb-2">Backend Settings</h2>
      <p className="text-text-muted text-sm mb-6 pb-6 border-b border-border">
        Configure API integrations and advanced system settings dynamically without restarting the server.
      </p>

      {success && (
        <div className="mb-6 flex items-center gap-2 px-4 py-3 rounded-lg bg-emerald-light text-emerald text-sm font-medium">
          <Check className="w-4 h-4" /> Settings updated successfully!
        </div>
      )}

      {loading ? (
        <div className="flex items-center gap-2 text-text-muted">
          <Loader2 className="w-4 h-4 animate-spin" /> Loading settings...
        </div>
      ) : (
        <div className="space-y-4">
          <div>
            <label className="text-xs text-text-light uppercase tracking-wide mb-1.5 block font-semibold">
              Groq API Key (AI Processing)
            </label>
            <input 
              type="password" 
              value={groqKey} 
              onChange={(e) => setGroqKey(e.target.value)} 
              className="input-heritage w-full font-mono text-sm" 
              placeholder="gsk_..." 
            />
            <p className="text-xs text-text-muted mt-2">
              Used by the backend to parse unstructured form submissions into standard JSON lineage using the LLaMa-3 model.
            </p>
          </div>

          <div className="pt-4">
            <button onClick={handleSave} disabled={saving} className="btn-primary">
              {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : "Save Settings"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

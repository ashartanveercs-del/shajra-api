"use client";

import AdminTreeEditor from "@/components/AdminTreeEditor";
import AsyncState from "@/components/feedback/AsyncState";

import { useState, useEffect, useCallback, useRef } from "react";
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
  adminFetchIntegrations,
  adminUndo,
  type AdminIntegrations,
  type PendingSubmission,
  type Member,
  type ApprovedEmail,
} from "@/lib/api";
import { asApiProblem, type Loadable } from "@/lib/loadable";
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
} from "lucide-react";

type Tab = "pending" | "members" | "tree" | "add" | "emails" | "integrations";
type DashboardSection = "pending" | "members" | "emails";

const INTEGRATION_ROWS = [
  ["Groq", "groqConfigured"],
  ["Cloudinary", "cloudinaryConfigured"],
  ["Coordination", "coordinationConfigured"],
] as const satisfies ReadonlyArray<readonly [string, keyof AdminIntegrations]>;

type DashboardData = {
  pending: PendingSubmission[];
  members: Member[];
  emails: ApprovedEmail[];
  unavailable: Set<DashboardSection>;
};

const EMPTY_DASHBOARD: DashboardData = {
  pending: [],
  members: [],
  emails: [],
  unavailable: new Set(),
};

function fetchDashboard(token: string) {
  return Promise.allSettled([
    fetchPending(token),
    fetchMembers(),
    adminFetchApprovedEmails(token),
  ] as const);
}

function dashboardStateFrom(results: Awaited<ReturnType<typeof fetchDashboard>>): Loadable<DashboardData> {
  const [pendingResult, membersResult, emailsResult] = results;
  const unavailable = new Set<DashboardSection>();
  if (pendingResult.status === "rejected") unavailable.add("pending");
  if (membersResult.status === "rejected") unavailable.add("members");
  if (emailsResult.status === "rejected") unavailable.add("emails");

  if (unavailable.size === results.length) {
    const firstFailure = results.find((result) => result.status === "rejected");
    return {
      status: "error",
      problem: asApiProblem(
        firstFailure?.status === "rejected" ? firstFailure.reason : undefined,
        "Admin data could not be loaded.",
      ),
    };
  }

  const data: DashboardData = {
    pending: pendingResult.status === "fulfilled" ? pendingResult.value : [],
    members: membersResult.status === "fulfilled" ? membersResult.value : [],
    emails: emailsResult.status === "fulfilled" ? emailsResult.value : [],
    unavailable,
  };

  if (unavailable.size > 0) {
    const firstFailure = results.find((result) => result.status === "rejected");
    return {
      status: "partial",
      data,
      problem: asApiProblem(
        firstFailure?.status === "rejected" ? firstFailure.reason : undefined,
        "Some admin data could not be loaded.",
      ),
    };
  }

  return data.pending.length === 0 && data.members.length === 0 && data.emails.length === 0
    ? { status: "empty", data }
    : { status: "ready", data };
}

function dashboardStateFor(data: DashboardData, problem?: ReturnType<typeof asApiProblem>): Loadable<DashboardData> {
  if (data.unavailable.size > 0) {
    return {
      status: "partial",
      data,
      problem: problem ?? asApiProblem(undefined, "Some admin data could not be loaded."),
    };
  }
  return data.pending.length === 0 && data.members.length === 0 && data.emails.length === 0
    ? { status: "empty", data }
    : { status: "ready", data };
}

export default function AdminPage() {
  const [token, setToken] = useState<string | null>(() =>
    typeof window === "undefined" ? null : localStorage.getItem("shajra_admin_token"),
  );
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loginError, setLoginError] = useState("");
  const [loginLoading, setLoginLoading] = useState(false);
  const [tab, setTab] = useState<Tab>("pending");
  const [dashboardState, setDashboardState] = useState<Loadable<DashboardData>>({
    status: "loading",
  });
  const [refreshing, setRefreshing] = useState(false);
  const [retryingSections, setRetryingSections] = useState<Set<DashboardSection>>(new Set());
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const dashboardRequest = useRef(0);
  const sessionEpoch = useRef(0);
  const activeToken = useRef(token);
  const loginRequest = useRef(0);
  const loginInFlight = useRef(false);
  const retryingSectionsRef = useRef<Set<DashboardSection>>(new Set());

  const sessionIsCurrent = useCallback((epoch: number, expectedToken: string) => (
    sessionEpoch.current === epoch && activeToken.current === expectedToken
  ), []);

  const resetPendingState = useCallback(() => {
    retryingSectionsRef.current = new Set();
    setRetryingSections(new Set());
    setRefreshing(false);
    setActionLoading(null);
    setActionError(null);
  }, []);

  const handleLogin = async () => {
    if (loginInFlight.current) return;
    loginInFlight.current = true;
    const request = ++loginRequest.current;
    const epoch = sessionEpoch.current;
    setLoginLoading(true);
    setLoginError("");
    try {
      const t = await adminLogin(username, password);
      if (request !== loginRequest.current || epoch !== sessionEpoch.current) return;
      sessionEpoch.current += 1;
      dashboardRequest.current += 1;
      activeToken.current = t;
      resetPendingState();
      setDashboardState({ status: "loading" });
      setToken(t);
      localStorage.setItem("shajra_admin_token", t);
    } catch {
      if (request !== loginRequest.current || epoch !== sessionEpoch.current) return;
      setLoginError("Invalid username or password");
    } finally {
      if (request === loginRequest.current) {
        loginInFlight.current = false;
        setLoginLoading(false);
      }
    }
  };

  const handleLogout = () => {
    sessionEpoch.current += 1;
    dashboardRequest.current += 1;
    loginRequest.current += 1;
    loginInFlight.current = false;
    activeToken.current = null;
    resetPendingState();
    setLoginLoading(false);
    setDashboardState({ status: "loading" });
    setToken(null);
    localStorage.removeItem("shajra_admin_token");
  };

  const loadData = useCallback(async (block = false) => {
    if (!token || activeToken.current !== token) return;
    const epoch = sessionEpoch.current;
    const request = ++dashboardRequest.current;
    if (block) setDashboardState({ status: "loading" });
    setRefreshing(true);
    try {
      const results = await fetchDashboard(token);
      if (request !== dashboardRequest.current || !sessionIsCurrent(epoch, token)) return;
      setDashboardState(dashboardStateFrom(results));
    } finally {
      if (request === dashboardRequest.current && sessionIsCurrent(epoch, token)) setRefreshing(false);
    }
  }, [sessionIsCurrent, token]);

  const retrySection = useCallback(async (section: DashboardSection) => {
    if (!token || activeToken.current !== token || retryingSectionsRef.current.has(section)) return;
    const epoch = sessionEpoch.current;
    const request = ++dashboardRequest.current;
    retryingSectionsRef.current = new Set(retryingSectionsRef.current).add(section);
    setRetryingSections(new Set(retryingSectionsRef.current));

    try {
      let applyResult: (data: DashboardData) => DashboardData;
      if (section === "pending") {
        const pending = await fetchPending(token);
        applyResult = (data) => ({ ...data, pending });
      } else if (section === "members") {
        const members = await fetchMembers();
        applyResult = (data) => ({ ...data, members });
      } else {
        const emails = await adminFetchApprovedEmails(token);
        applyResult = (data) => ({ ...data, emails });
      }
      if (request !== dashboardRequest.current || !sessionIsCurrent(epoch, token)) return;
      setDashboardState((current) => {
        const retained = "data" in current ? current.data : EMPTY_DASHBOARD;
        const unavailable = new Set(retained.unavailable);
        unavailable.delete(section);
        return dashboardStateFor(applyResult({ ...retained, unavailable }));
      });
    } catch (error: unknown) {
      if (request !== dashboardRequest.current || !sessionIsCurrent(epoch, token)) return;
      setDashboardState((current) => {
        const retained = "data" in current ? current.data : EMPTY_DASHBOARD;
        const unavailable = new Set(retained.unavailable).add(section);
        return dashboardStateFor(
          { ...retained, unavailable },
          asApiProblem(error, "This admin section could not be loaded."),
        );
      });
    } finally {
      if (request === dashboardRequest.current && sessionIsCurrent(epoch, token)) {
        const next = new Set(retryingSectionsRef.current);
        next.delete(section);
        retryingSectionsRef.current = next;
        setRetryingSections(new Set(next));
      }
    }
  }, [sessionIsCurrent, token]);

  useEffect(() => {
    if (!token || activeToken.current !== token) return;
    const epoch = sessionEpoch.current;
    const request = ++dashboardRequest.current;
    fetchDashboard(token).then((results) => {
      if (request !== dashboardRequest.current || !sessionIsCurrent(epoch, token)) return;
      setDashboardState(dashboardStateFrom(results));
    });
    return () => {
      dashboardRequest.current += 1;
    };
  }, [sessionIsCurrent, token]);

  const handleApprove = async (id: string) => {
    if (!token) return;
    const epoch = sessionEpoch.current;
    setActionLoading(id);
    setActionError(null);
    try {
      await approveSubmission(token, id);
      if (!sessionIsCurrent(epoch, token)) return;
      await loadData();
    } catch (error: unknown) {
      if (sessionIsCurrent(epoch, token)) setActionError(asApiProblem(error, "The submission could not be approved.").message);
    } finally {
      if (sessionIsCurrent(epoch, token)) setActionLoading(null);
    }
  };

  const handleReject = async (id: string) => {
    if (!token) return;
    const epoch = sessionEpoch.current;
    setActionLoading(id);
    setActionError(null);
    try {
      await rejectSubmission(token, id);
      if (!sessionIsCurrent(epoch, token)) return;
      await loadData();
    } catch (error: unknown) {
      if (sessionIsCurrent(epoch, token)) setActionError(asApiProblem(error, "The submission could not be rejected.").message);
    } finally {
      if (sessionIsCurrent(epoch, token)) setActionLoading(null);
    }
  };

  const handleDelete = async (id: string) => {
    if (!token || !confirm("Delete this member permanently?")) return;
    const epoch = sessionEpoch.current;
    setActionLoading(id);
    setActionError(null);
    try {
      await adminDeleteMember(token, id);
      if (!sessionIsCurrent(epoch, token)) return;
      await loadData();
    } catch (error: unknown) {
      if (sessionIsCurrent(epoch, token)) setActionError(asApiProblem(error, "The member could not be deleted.").message);
    } finally {
      if (sessionIsCurrent(epoch, token)) setActionLoading(null);
    }
  };

  const handleUndo = async () => {
    if (!token) return;
    const epoch = sessionEpoch.current;
    setActionLoading("undo");
    setActionError(null);
    try {
      await adminUndo(token);
      if (!sessionIsCurrent(epoch, token)) return;
      await loadData();
    } catch (error: unknown) {
      if (sessionIsCurrent(epoch, token)) setActionError(asApiProblem(error, "The last change could not be undone.").message);
    } finally {
      if (sessionIsCurrent(epoch, token)) setActionLoading(null);
    }
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
              <label htmlFor="admin-username" className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Username</label>
              <input id="admin-username" type="text" value={username} onChange={(e) => setUsername(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleLogin()} className="input-heritage" placeholder="admin" />
            </div>
            <div>
              <label htmlFor="admin-password" className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Password</label>
              <input id="admin-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} onKeyDown={(e) => e.key === "Enter" && handleLogin()} className="input-heritage" placeholder="Enter password" />
            </div>
            {loginError && (
              <div role="alert" className="flex items-center gap-2 text-terracotta text-sm bg-terracotta-light p-3 rounded-lg">
                <AlertTriangle className="w-4 h-4" />
                {loginError}
              </div>
            )}
            <button type="button" onClick={handleLogin} disabled={loginLoading} className="btn-primary w-full justify-center py-3">
              {loginLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <LogIn className="w-4 h-4" />}
              Sign In
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Dashboard ──
  const dashboard = "data" in dashboardState ? dashboardState.data : EMPTY_DASHBOARD;
  const { pending, members, emails, unavailable } = dashboard;
  const pendingOnly = pending.filter((p) => p.Status === "Pending");

  return (
    <div className="mx-auto max-w-5xl px-5 sm:px-8 py-10 sm:py-14">
      <div className="flex items-center justify-between mb-8">
        <div>
          <h1 className="heading-serif text-2xl sm:text-3xl font-bold">Admin Dashboard</h1>
          <p className="text-text-muted text-sm mt-1">Manage submissions, members, and access</p>
        </div>
        <button type="button" onClick={handleLogout} className="flex items-center gap-1.5 px-3 py-2 text-sm text-text-muted hover:text-terracotta hover:bg-terracotta-light rounded-lg transition-heritage">
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
          { key: "integrations", label: "Integrations", icon: Settings },
        ].map((t) => (
          <button
            type="button"
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
        <button
          type="button"
          onClick={() => loadData()}
          disabled={refreshing}
          className="ml-auto flex-shrink-0 pl-4 text-xs text-text-light transition-heritage hover:text-accent focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {refreshing ? <Loader2 aria-label="Refreshing dashboard" className="h-3.5 w-3.5 animate-spin" /> : "Refresh"}
        </button>
      </div>

      {/* Quick Actions Bar */}
      <div className="flex items-center gap-2 mb-5 flex-wrap">
        <button
          type="button"
          onClick={handleUndo}
          disabled={actionLoading === "undo"}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-text-muted hover:text-accent hover:bg-accent/5 border border-border rounded-lg transition-heritage"
        >
          {actionLoading === "undo" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Undo2 className="w-3.5 h-3.5" />}
          Undo Last Change
        </button>
      </div>

      {actionError && (
        <div role="alert" className="mb-5 rounded-lg border border-terracotta-light bg-terracotta-light/30 p-3 text-sm text-terracotta">
          {actionError}
        </div>
      )}

      {dashboardState.status === "partial" && (
        <div role="status" className="mb-5 rounded-lg border border-terracotta-light bg-terracotta-light/20 p-3">
          <p className="text-sm font-medium text-text-primary">Some dashboard data is unavailable</p>
          <p className="mt-1 text-xs text-text-muted">{dashboardState.problem.message}</p>
        </div>
      )}

      {dashboardState.status === "loading" && <AsyncState state="loading" title="Loading admin data" />}
      {dashboardState.status === "error" && (
        <AsyncState state="error" title="Admin data unavailable" message={dashboardState.problem.message} actionLabel="Retry" onAction={() => loadData(true)} />
      )}

      {dashboardState.status !== "loading" && dashboardState.status !== "error" && (
        <>
          {tab === "pending" && (unavailable.has("pending") ? <AdminSectionUnavailable title="Submissions unavailable" pending={retryingSections.has("pending")} onRetry={() => void retrySection("pending")} /> : <PendingTab submissions={pendingOnly} actionLoading={actionLoading} onApprove={handleApprove} onReject={handleReject} />)}
          {tab === "members" && (unavailable.has("members") ? <AdminSectionUnavailable title="Members unavailable" pending={retryingSections.has("members")} onRetry={() => void retrySection("members")} /> : <MembersTab members={members} actionLoading={actionLoading} onDelete={handleDelete} />)}
          {tab === "tree" && <AdminTreeEditor token={token} onUpdated={() => loadData()} />}
          {tab === "add" && (unavailable.has("members") ? <AdminSectionUnavailable title="Members unavailable" pending={retryingSections.has("members")} onRetry={() => void retrySection("members")} /> : <AddMemberTab token={token} onCreated={() => loadData()} members={members} />)}
          {tab === "emails" && (unavailable.has("emails") ? <AdminSectionUnavailable title="Approved emails unavailable" pending={retryingSections.has("emails")} onRetry={() => void retrySection("emails")} /> : <EmailsTab emails={emails} token={token} onUpdated={() => loadData()} />)}
          {tab === "integrations" && <IntegrationsTab token={token} />}
        </>
      )}
    </div>
  );
}

function AdminSectionUnavailable({ title, pending, onRetry }: { title: string; pending: boolean; onRetry: () => void }) {
  return (
    <div role="alert" className="flex min-h-[11rem] w-full flex-col items-center justify-center px-5 py-8 text-center">
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-lg bg-bg-secondary text-text-light">
        <AlertTriangle aria-hidden="true" className="h-5 w-5" />
      </div>
      <h2 className="font-serif text-xl font-semibold text-text-primary">{title}</h2>
      <p className="mt-2 max-w-md text-sm leading-relaxed text-text-muted">This section could not be loaded.</p>
      <button type="button" onClick={onRetry} disabled={pending} className="btn-secondary mt-5 min-h-10 px-4">
        {pending && <Loader2 aria-hidden="true" className="h-4 w-4 animate-spin" />}
        Retry
      </button>
    </div>
  );
}

function EmailsTab({ emails, token, onUpdated }: { emails: ApprovedEmail[]; token: string; onUpdated: () => void }) {
  const [newEmail, setNewEmail] = useState("");
  const [newName, setNewName] = useState("");
  const [loadingId, setLoadingId] = useState<string|null>(null);
  const [emailError, setEmailError] = useState<string | null>(null);

  const handleAdd = async () => {
    if (!newEmail) return;
    setLoadingId("add");
    setEmailError(null);
    try {
      await adminAddApprovedEmail(token, newEmail, newName);
      setNewEmail("");
      setNewName("");
      onUpdated();
    } catch (error: unknown) {
      setEmailError(asApiProblem(error, "The approved email could not be added.").message);
    } finally {
      setLoadingId(null);
    }
  };

  const handleRemove = async (id: string) => {
    if (!confirm("Remove this email?")) return;
    setLoadingId(id);
    setEmailError(null);
    try {
      await adminDeleteApprovedEmail(token, id);
      onUpdated();
    } catch (error: unknown) {
      setEmailError(asApiProblem(error, "The approved email could not be removed.").message);
    } finally {
      setLoadingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="heritage-card p-6 py-8">
        <h2 className="font-serif text-lg font-semibold mb-2">Authorize Family Members</h2>
        <p className="text-text-muted text-sm mb-6 max-w-2xl">
          To prevent spam and protect privacy, only users whose email is on this list can leave comments or post stories on profiles.
        </p>

        {emailError && <div role="alert" className="mb-4 rounded-lg border border-terracotta-light bg-terracotta-light/30 p-3 text-sm text-terracotta">{emailError}</div>}
        <div className="flex flex-col sm:flex-row gap-3">
          <label className="sr-only" htmlFor="approved-email-name">Name</label>
          <input id="approved-email-name" type="text" placeholder="Name (optional)" value={newName} onChange={e=>setNewName(e.target.value)} className="input-heritage sm:w-1/3" />
          <label className="sr-only" htmlFor="approved-email-address">Email</label>
          <input id="approved-email-address" type="email" placeholder="name@example.com *" value={newEmail} onChange={e=>setNewEmail(e.target.value)} className="input-heritage flex-1" />
          <button type="button" onClick={handleAdd} disabled={!newEmail || loadingId === "add"} className="btn-primary whitespace-nowrap">
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
                    <button type="button" aria-label={`Remove ${e.Email}`} onClick={() => handleRemove(e.id)} disabled={loadingId === e.id} className="text-text-light hover:text-terracotta p-2 rounded-lg hover:bg-terracotta/10 transition-colors focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
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

function submissionName(submission: PendingSubmission): string {
  return submission.CleanFullName || submission.RawFullName || "Unknown";
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
                <h3 className="font-serif font-semibold text-lg text-text-primary">{submissionName(sub)}</h3>
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
              <button type="button" aria-label={`View ${submissionName(sub)} details`} onClick={() => setExpanded(expanded === sub.id ? null : sub.id)} className="p-2 rounded-lg text-text-light hover:text-text-primary hover:bg-bg-secondary transition-heritage">
                <Eye aria-hidden="true" className="w-4 h-4" />
              </button>
              <button type="button" aria-label={`Approve ${submissionName(sub)}`} onClick={() => onApprove(sub.id)} disabled={actionLoading === sub.id} className="flex items-center gap-1 px-3 py-2 bg-emerald-light text-emerald rounded-lg text-xs font-medium hover:bg-emerald hover:text-white transition-heritage disabled:opacity-50">
                {actionLoading === sub.id ? <Loader2 aria-hidden="true" className="w-3.5 h-3.5 animate-spin" /> : <Check aria-hidden="true" className="w-3.5 h-3.5" />} Approve
              </button>
              <button type="button" aria-label={`Reject ${submissionName(sub)}`} onClick={() => onReject(sub.id)} disabled={actionLoading === sub.id} className="flex items-center gap-1 px-3 py-2 bg-terracotta-light text-terracotta rounded-lg text-xs font-medium hover:bg-terracotta hover:text-white transition-heritage disabled:opacity-50">
                <X aria-hidden="true" className="w-3.5 h-3.5" /> Reject
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
          <button type="button" aria-label={`Delete ${m.FullName || "member"}`} onClick={() => onDelete(m.id)} disabled={actionLoading === m.id} className="p-2 text-text-light hover:text-terracotta hover:bg-terracotta-light rounded-lg transition-heritage">
            {actionLoading === m.id ? <Loader2 aria-hidden="true" className="w-4 h-4 animate-spin" /> : <Trash2 aria-hidden="true" className="w-4 h-4" />}
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
  const [formError, setFormError] = useState<string | null>(null);
  const successTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const clearSuccess = useCallback(() => {
    if (successTimer.current !== null) {
      clearTimeout(successTimer.current);
      successTimer.current = null;
    }
    setSuccess(false);
  }, []);

  useEffect(() => () => {
    if (successTimer.current !== null) clearTimeout(successTimer.current);
  }, []);

  const handleSubmit = async () => {
    clearSuccess();
    if (!form.FullName.trim()) {
      setFormError("Full Name is required.");
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      const fields: Record<string, unknown> = { ...form };
      if (form.Generation) fields.Generation = parseInt(form.Generation);
      else delete fields.Generation;
      Object.keys(fields).forEach((k) => { if (fields[k] === "") delete fields[k]; });
      await adminCreateMember(token, fields as Partial<Member>);
      setSuccess(true);
      setForm({ FullName: "", FatherName: "", MotherName: "", SpouseName: "", DateOfBirth: "", DateOfDeath: "", CurrentCity: "", CurrentCountry: "", BurialLocation: "", Biography: "", Gender: "", Generation: "", Branch: "", FatherRecordId: "", MotherRecordId: "", SpouseRecordId: "", IsAlive: true });
      onCreated();
      successTimer.current = setTimeout(() => {
        successTimer.current = null;
        setSuccess(false);
      }, 3000);
    } catch (error: unknown) {
      setFormError(asApiProblem(error, "The member could not be added.").message);
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="heritage-card p-7 max-w-3xl animate-fadeInUp">
      <h2 className="font-serif text-xl font-semibold mb-6 text-text-primary">Add New Member</h2>

      {success && (
        <div className="mb-6 flex items-center gap-2 px-4 py-3 rounded-lg bg-emerald-light text-emerald text-sm font-medium">
          <Check className="w-4 h-4" /> Member added successfully!
        </div>
      )}

      {formError && (
        <div role="alert" className="mb-6 rounded-lg border border-terracotta-light bg-terracotta-light/30 px-4 py-3 text-sm text-terracotta">
          {formError}
        </div>
      )}

      <div className="grid sm:grid-cols-2 gap-4" onChangeCapture={clearSuccess}>
        <div><label htmlFor="admin-member-name" className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Full Name *</label><input id="admin-member-name" type="text" value={form.FullName} onChange={(e) => setForm({ ...form, FullName: e.target.value })} className="input-heritage" placeholder="Muhammad Ali Khan" /></div>
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

      <div className="mt-4" onChangeCapture={clearSuccess}><label className="text-[11px] text-text-light uppercase tracking-wide mb-1.5 block">Biography</label><textarea value={form.Biography} onChange={(e) => setForm({ ...form, Biography: e.target.value })} rows={3} className="input-heritage" placeholder="Notes about this family member..." /></div>

      <button type="button" aria-label="Create member record" onClick={handleSubmit} disabled={saving} className="btn-primary mt-6">
        {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plus className="w-4 h-4" />}
        Add Member
      </button>
    </div>
  );
}

function IntegrationsTab({ token }: { token: string }) {
  const [integrationState, setIntegrationState] = useState<Loadable<AdminIntegrations>>({
    status: "loading",
  });
  const integrationRequest = useRef(0);

  const loadIntegrations = useCallback(() => {
    const request = ++integrationRequest.current;
    adminFetchIntegrations(token).then(
      (data) => {
        if (request !== integrationRequest.current) return;
        setIntegrationState({ status: "ready", data });
      },
      (error: unknown) => {
        if (request !== integrationRequest.current) return;
        setIntegrationState({
          status: "error",
          problem: asApiProblem(error, "Integration status could not be loaded."),
        });
      },
    );
  }, [token]);

  useEffect(() => {
    loadIntegrations();
    return () => {
      integrationRequest.current += 1;
    };
  }, [loadIntegrations]);

  const retryIntegrations = () => {
    setIntegrationState({ status: "loading" });
    loadIntegrations();
  };

  return (
    <div className="max-w-2xl rounded-lg border border-border bg-bg-card p-7 shadow-card animate-fadeInUp">
      <h2 className="font-serif text-xl font-semibold mb-2">Integration Status</h2>
      <p className="text-text-muted text-sm mb-6 pb-6 border-b border-border">
        Backend integrations are configured outside this application.
      </p>

      {integrationState.status === "loading" && <AsyncState state="loading" title="Loading integration status" />}
      {integrationState.status === "error" && <AsyncState state="error" title="Integration status unavailable" message={integrationState.problem.message} actionLabel="Retry" onAction={retryIntegrations} />}
      {integrationState.status === "ready" && (
        <dl className="divide-y divide-border">
          {INTEGRATION_ROWS.map(([label, key]) => {
            const configured = integrationState.data[key];
            return (
              <div key={key} className="flex items-center justify-between gap-4 py-4 first:pt-0 last:pb-0">
                <dt className="text-sm font-medium text-text-primary">{label}</dt>
                <dd className={configured ? "text-sm font-medium text-emerald" : "text-sm font-medium text-text-muted"}>
                  {configured ? "Configured" : "Not configured"}
                </dd>
              </div>
            );
          })}
        </dl>
      )}
    </div>
  );
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface Member {
  id: string;
  FullName?: string;
  FatherName?: string;
  MotherName?: string;
  SpouseName?: string;
  DateOfBirth?: string;
  DateOfDeath?: string;
  CurrentCity?: string;
  CurrentCountry?: string;
  BurialLocation?: string;
  Latitude?: number;
  Longitude?: number;
  BurialLatitude?: number;
  BurialLongitude?: number;
  Biography?: string;
  Autobiography?: string;
  HeritageStory?: string;
  Photos?: { url: string; filename: string }[];
  Generation?: number;
  FatherRecordId?: string;
  MotherRecordId?: string;
  SpouseRecordId?: string;
  Gender?: string;
  IsAlive?: boolean;
  Branch?: string;
  Email?: string;
  PhoneNumber?: string;
  ProfileImageUrl?: string;
  children?: Member[];
}

export interface PendingSubmission {
  id: string;
  RawFullName?: string;
  RawFatherName?: string;
  RawMotherName?: string;
  RawSpouseName?: string;
  RawDateOfBirth?: string;
  RawDateOfDeath?: string;
  RawLocation?: string;
  RawBurialLocation?: string;
  RawBiography?: string;
  RawGender?: string;
  SubmittedAt?: string;
  CleanFullName?: string;
  CleanFatherName?: string;
  CleanMotherName?: string;
  CleanSpouseName?: string;
  CleanDOB?: string;
  CleanDOD?: string;
  CleanCity?: string;
  CleanCountry?: string;
  CleanBurialLocation?: string;
  CleanGender?: string;
  AIMatchedFatherId?: string;
  AIMatchedMotherId?: string;
  AIMatchedSpouseId?: string;
  AIConfidence?: number;
  AIDuplicateFlag?: boolean;
  AINotes?: string;
  RawEmail?: string;
  RawPhoneNumber?: string;
  RawProfileImage?: string;
  CleanEmail?: string;
  CleanPhoneNumber?: string;
  CleanProfileImage?: string;
  Status?: string;
}

export interface Comment {
  id: string;
  CommentText: string;
  AuthorName: string;
  AuthorEmail?: string;
  MemberRecordId: string;
  MemberName?: string;
  CreatedAt?: string;
}

export interface Story {
  id: string;
  Title: string;
  Content: string;
  AuthorName: string;
  AuthorEmail?: string;
  MemberRecordId?: string;
  MemberName?: string;
  StoryType?: string;
  Photos?: { url: string; filename: string }[];
  CreatedAt?: string;
}

export interface Album {
  id: string;
  MemberRecordId?: string;
  MemberName?: string;
  ImageUrl?: string | { url: string; filename: string }[];
  Caption?: string;
  UploadedAt?: string;
}

export interface ApprovedEmail {
  id: string;
  Email: string;
  Name?: string;
  Notes?: string;
  AddedAt?: string;
}

export interface MapMarker {
  id: string;
  name: string;
  type: "residence" | "burial";
  lat: number;
  lng: number;
  city?: string;
  country?: string;
  location?: string;
  gender?: string;
  isAlive?: boolean;
}

export interface MapArc {
  startLat: number;
  startLng: number;
  endLat: number;
  endLng: number;
  label?: string;
  color?: string;
}

export interface MapData {
  markers: MapMarker[];
  arcs: MapArc[];
}

// ── Public API ──────────────────────────────────────────────

export async function fetchMembers(): Promise<Member[]> {
  const res = await fetch(`${API_BASE}/api/members`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch members");
  return res.json();
}

export async function fetchMember(id: string): Promise<Member> {
  const res = await fetch(`${API_BASE}/api/members/${id}`, { cache: "no-store" });
  if (!res.ok) throw new Error("Member not found");
  return res.json();
}

export async function fetchTree(): Promise<Member[]> {
  const res = await fetch(`${API_BASE}/api/tree`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch tree");
  return res.json();
}

export async function fetchMapMarkers(): Promise<MapData> {
  const res = await fetch(`${API_BASE}/api/map-markers`, { cache: "no-store" });
  if (!res.ok) throw new Error("Failed to fetch map data");
  return res.json();
}

export async function searchMembers(query: string): Promise<Member[]> {
  const res = await fetch(`${API_BASE}/api/search?q=${encodeURIComponent(query)}`, { cache: "no-store" });
  if (!res.ok) return [];
  return res.json();
}

// ── Comments API ────────────────────────────────────────────

export async function fetchComments(memberId: string): Promise<Comment[]> {
  const res = await fetch(`${API_BASE}/api/comments/${memberId}`, { cache: "no-store" });
  if (!res.ok) return [];
  return res.json();
}

export async function postComment(data: Partial<Comment>): Promise<Comment> {
  const res = await fetch(`${API_BASE}/api/comments`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to post comment");
  }
  return res.json();
}

export async function verifyEmail(email: string): Promise<boolean> {
  try {
    const res = await fetch(`${API_BASE}/api/verify-email?email=${encodeURIComponent(email)}`, { cache: "no-store" });
    if (!res.ok) return false;
    const data = await res.json();
    return data.approved === true;
  } catch {
    return false;
  }
}

// ── Stories & Albums API ────────────────────────────────────

export async function fetchStories(memberId?: string): Promise<Story[]> {
  const url = memberId ? `${API_BASE}/api/stories/member/${memberId}` : `${API_BASE}/api/stories`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) return [];
  return res.json();
}

export async function postStory(data: Partial<Story>): Promise<Story> {
  const res = await fetch(`${API_BASE}/api/stories`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Failed to post story");
  }
  return res.json();
}

export async function fetchAlbums(memberId?: string): Promise<Album[]> {
  const url = memberId ? `${API_BASE}/api/albums/member/${memberId}` : `${API_BASE}/api/albums`;
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) return [];
  return res.json();
}

export async function uploadAlbumPhoto(data: Partial<Album>): Promise<Album> {
  const res = await fetch(`${API_BASE}/api/albums`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error("Failed to upload photo");
  return res.json();
}

// ── Form Submission API ─────────────────────────────────────

export async function submitDirectForm(data: any): Promise<any> {
  const res = await fetch(`${API_BASE}/api/submit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || "Form submission failed");
  }
  return res.json();
}

// ── Admin API ───────────────────────────────────────────────

function authHeaders(token: string) {
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
}

export async function adminLogin(username: string, password: string): Promise<string> {
  const res = await fetch(`${API_BASE}/api/admin/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) throw new Error("Invalid credentials");
  const data = await res.json();
  return data.access_token;
}

export async function fetchPending(token: string): Promise<PendingSubmission[]> {
  const res = await fetch(`${API_BASE}/api/admin/pending`, {
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error("Failed to fetch pending");
  return res.json();
}

export async function approveSubmission(token: string, recordId: string) {
  const res = await fetch(`${API_BASE}/api/admin/approve/${recordId}`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error("Failed to approve");
  return res.json();
}

export async function rejectSubmission(token: string, recordId: string) {
  const res = await fetch(`${API_BASE}/api/admin/reject/${recordId}`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error("Failed to reject");
  return res.json();
}

export async function adminCreateMember(token: string, fields: Partial<Member>) {
  const res = await fetch(`${API_BASE}/api/admin/members`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(fields),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to create member");
  }
  return res.json();
}

export async function adminUpdateMember(token: string, recordId: string, fields: Partial<Member>) {
  const res = await fetch(`${API_BASE}/api/admin/members/${recordId}`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(fields),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || "Failed to update member");
  }
  return res.json();
}

export async function adminDeleteMember(token: string, recordId: string) {
  const res = await fetch(`${API_BASE}/api/admin/members/${recordId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error("Failed to delete member");
  return res.json();
}

export async function adminFetchApprovedEmails(token: string): Promise<ApprovedEmail[]> {
  const res = await fetch(`${API_BASE}/api/admin/approved-emails`, {
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error("Failed to fetch emails");
  return res.json();
}

export async function adminAddApprovedEmail(token: string, email: string, name: string = "", notes: string = "") {
  const res = await fetch(`${API_BASE}/api/admin/approved-emails`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ Email: email, Name: name, Notes: notes }),
  });
  if (!res.ok) throw new Error("Failed to add email");
  return res.json();
}

export async function adminDeleteApprovedEmail(token: string, recordId: string) {
  const res = await fetch(`${API_BASE}/api/admin/approved-emails/${recordId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error("Failed to delete email");
  return res.json();
}

export async function adminFetchSettings(token: string) {
  const res = await fetch(`${API_BASE}/api/admin/settings`, {
    headers: authHeaders(token),
  });
  if (!res.ok) throw new Error("Failed to fetch settings");
  return res.json();
}

export async function adminUpdateSettings(token: string, settings: { GROQ_API_KEY: string }) {
  const res = await fetch(`${API_BASE}/api/admin/settings`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(settings),
  });
  if (!res.ok) throw new Error("Failed to update settings");
  return res.json();
}

export async function uploadImage(file: File) {
  const formData = new FormData();
  formData.append("file", file);
  const res = await fetch(`${API_BASE}/api/upload-image`, {
    method: "POST",
    body: formData,
  });
  if (!res.ok) {
    const error = await res.json();
    throw new Error(error.detail || "Failed to upload image");
  }
  return res.json();
}

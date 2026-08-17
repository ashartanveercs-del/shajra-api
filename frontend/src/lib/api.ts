import { requestJson } from "./http";

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
  CardStyle?: string;
  IsPlaceholder?: boolean;
  _isSpouseRef?: boolean;
  Spouse?: Member;
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

export interface SearchFilters {
  city?: string;
  branch?: string;
  generation?: string;
}

export interface ImageUpload {
  url: string;
}

interface AdminUndoResult {
  action?: string;
}

// ── Public API ──────────────────────────────────────────────

export function fetchMembers(): Promise<Member[]> {
  return requestJson<Member[]>("/api/members", { cache: "no-store" });
}

export function fetchMember(id: string): Promise<Member> {
  return requestJson<Member>(`/api/members/${id}`, { cache: "no-store" });
}

export function fetchTree(): Promise<Member[]> {
  return requestJson<Member[]>("/api/tree", { cache: "no-store" });
}

export function fetchMapMarkers(): Promise<MapData> {
  return requestJson<MapData>("/api/map-markers", { cache: "no-store" });
}

export function searchMembers(
  query: string,
  filters: SearchFilters = {},
): Promise<Member[]> {
  const params = new URLSearchParams();
  if (query) params.set("q", query);
  if (filters.city) params.set("city", filters.city);
  if (filters.branch) params.set("branch", filters.branch);
  if (filters.generation) params.set("generation", filters.generation);
  return requestJson<Member[]>(`/api/search?${params.toString()}`, {
    cache: "no-store",
  });
}

// ── Comments API ────────────────────────────────────────────

export function fetchComments(memberId: string): Promise<Comment[]> {
  return requestJson<Comment[]>(`/api/comments/${memberId}`, { cache: "no-store" });
}

export function postComment(data: Partial<Comment>): Promise<Comment> {
  return requestJson<Comment>("/api/comments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function verifyEmail(email: string): Promise<boolean> {
  return requestJson<{ approved?: boolean }>(
    `/api/verify-email?email=${encodeURIComponent(email)}`,
    { cache: "no-store" },
  ).then((data) => data.approved === true);
}

// ── Stories & Albums API ────────────────────────────────────

export function fetchStories(memberId?: string): Promise<Story[]> {
  const path = memberId ? `/api/stories/member/${memberId}` : "/api/stories";
  return requestJson<Story[]>(path, { cache: "no-store" });
}

export function postStory(data: Partial<Story>): Promise<Story> {
  return requestJson<Story>("/api/stories", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

export function fetchAlbums(memberId?: string): Promise<Album[]> {
  const path = memberId ? `/api/albums/member/${memberId}` : "/api/albums";
  return requestJson<Album[]>(path, { cache: "no-store" });
}

export function uploadAlbumPhoto(data: Partial<Album>): Promise<Album> {
  return requestJson<Album>("/api/albums", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

// ── Form Submission API ─────────────────────────────────────

export function submitDirectForm(data: unknown): Promise<unknown> {
  return requestJson<unknown>("/api/submit", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
}

// ── Admin API ───────────────────────────────────────────────

function authHeaders(token: string) {
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json",
  };
}

export async function adminLogin(username: string, password: string): Promise<string> {
  const data = await requestJson<{ access_token: string }>("/api/admin/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  return data.access_token;
}

export function fetchPending(token: string): Promise<PendingSubmission[]> {
  return requestJson<PendingSubmission[]>("/api/admin/pending", {
    headers: authHeaders(token),
  });
}

export function approveSubmission(token: string, recordId: string): Promise<unknown> {
  return requestJson<unknown>(`/api/admin/approve/${recordId}`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function rejectSubmission(token: string, recordId: string): Promise<unknown> {
  return requestJson<unknown>(`/api/admin/reject/${recordId}`, {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function adminCreateMember(token: string, fields: Partial<Member>): Promise<Member> {
  return requestJson<Member>("/api/admin/members", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify(fields),
  });
}

export function adminUpdateMember(
  token: string,
  recordId: string,
  fields: Partial<Member>,
): Promise<Member> {
  return requestJson<Member>(`/api/admin/members/${recordId}`, {
    method: "PUT",
    headers: authHeaders(token),
    body: JSON.stringify(fields),
  });
}

export function adminDeleteMember(token: string, recordId: string): Promise<unknown> {
  return requestJson<unknown>(`/api/admin/members/${recordId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}

export function adminFetchApprovedEmails(token: string): Promise<ApprovedEmail[]> {
  return requestJson<ApprovedEmail[]>("/api/admin/approved-emails", {
    headers: authHeaders(token),
  });
}

export function adminAddApprovedEmail(
  token: string,
  email: string,
  name: string = "",
  notes: string = "",
): Promise<ApprovedEmail> {
  return requestJson<ApprovedEmail>("/api/admin/approved-emails", {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ Email: email, Name: name, Notes: notes }),
  });
}

export function adminDeleteApprovedEmail(token: string, recordId: string): Promise<unknown> {
  return requestJson<unknown>(`/api/admin/approved-emails/${recordId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
}

export interface AdminIntegrations {
  groqConfigured: boolean;
  cloudinaryConfigured: boolean;
  coordinationConfigured: boolean;
}

export function adminFetchIntegrations(token: string): Promise<AdminIntegrations> {
  return requestJson<AdminIntegrations>("/api/admin/integrations", {
    headers: authHeaders(token),
  });
}

export function uploadImage(file: File): Promise<ImageUpload> {
  const formData = new FormData();
  formData.append("file", file);
  return requestJson<ImageUpload>("/api/upload-image", {
    method: "POST",
    body: formData,
  });
}

// ── Admin: Undo / Heal ──────────────────────────────────────────────

export function adminUndo(token: string): Promise<AdminUndoResult> {
  return requestJson<AdminUndoResult>("/api/admin/undo", {
    method: "POST",
    headers: authHeaders(token),
  });
}

export function adminGetHistory(token: string): Promise<unknown[]> {
  return requestJson<unknown[]>("/api/admin/history", {
    headers: authHeaders(token),
  });
}

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import {
  fetchAlbums,
  fetchComments,
  fetchMember,
  fetchMembers,
  postComment,
  uploadAlbumPhoto,
  uploadImage,
  verifyEmail,
  type Album,
  type Comment,
  type Member,
} from "@/lib/api";
import AsyncState from "@/components/feedback/AsyncState";
import { asApiProblem, type Loadable } from "@/lib/loadable";
import {
  ArrowLeft,
  Heart,
  MapPin,
  Calendar,
  User,
  Users,
  Loader2,
  ExternalLink,
  BookOpen,
  MessageSquare,
  Lock,
  Send,
  Camera,
  Plus
} from "lucide-react";

type AuxiliarySection = "relationships" | "comments" | "albums";

type MemberProfileData = {
  member: Member;
  allMembers: Member[];
  comments: Comment[];
  albums: Album[];
  unavailable: Set<AuxiliarySection>;
};

type StoredProfileState = {
  requestedId: string;
  load: Loadable<MemberProfileData, null>;
};

type AuxiliaryData = Member[] | Comment[] | Album[];

export default function MemberProfilePage() {
  const params = useParams();
  const id = params.id as string;
  const [storedProfile, setStoredProfile] = useState<StoredProfileState>({
    requestedId: id,
    load: { status: "loading" },
  });
  const profileRequest = useRef(0);
  const auxiliaryRequests = useRef<Record<AuxiliarySection, number>>({
    relationships: 0,
    comments: 0,
    albums: 0,
  });
  const profileState: Loadable<MemberProfileData, null> =
    storedProfile.requestedId === id ? storedProfile.load : { status: "loading" };

  const loadProfile = useCallback((requestedId: string) => {
    const request = ++profileRequest.current;
    Promise.allSettled([
      fetchMember(requestedId),
      fetchMembers(),
      fetchComments(requestedId),
      fetchAlbums(requestedId),
    ] as const).then((results) => {
      if (request !== profileRequest.current) return;

      const [memberResult, membersResult, commentsResult, albumsResult] = results;
      if (memberResult.status === "rejected") {
        const problem = asApiProblem(memberResult.reason, "This member could not be loaded.");
        setStoredProfile({
          requestedId,
          load:
            problem.status === 404
              ? { status: "empty", data: null }
              : { status: "error", problem },
        });
        return;
      }

      const unavailable = new Set<AuxiliarySection>();
      if (membersResult.status === "rejected") unavailable.add("relationships");
      if (commentsResult.status === "rejected") unavailable.add("comments");
      if (albumsResult.status === "rejected") unavailable.add("albums");

      const data: MemberProfileData = {
        member: memberResult.value,
        allMembers: membersResult.status === "fulfilled" ? membersResult.value : [],
        comments: commentsResult.status === "fulfilled" ? commentsResult.value : [],
        albums: albumsResult.status === "fulfilled" ? albumsResult.value : [],
        unavailable,
      };

      if (unavailable.size === 0) {
        setStoredProfile({ requestedId, load: { status: "ready", data } });
        return;
      }

      const firstFailure = [membersResult, commentsResult, albumsResult].find(
        (result) => result.status === "rejected",
      );
      setStoredProfile({
        requestedId,
        load: {
          status: "partial",
          data,
          problem: asApiProblem(
            firstFailure?.status === "rejected" ? firstFailure.reason : undefined,
            "Some profile details could not be loaded.",
          ),
        },
      });
    });
  }, []);

  useEffect(() => {
    const requests = auxiliaryRequests.current;
    loadProfile(id);
    return () => {
      profileRequest.current += 1;
      requests.relationships += 1;
      requests.comments += 1;
      requests.albums += 1;
    };
  }, [id, loadProfile]);

  const retryProfile = () => {
    setStoredProfile({ requestedId: id, load: { status: "loading" } });
    loadProfile(id);
  };

  const mergeAuxiliaryData = (
    requestedId: string,
    section: AuxiliarySection,
    value: AuxiliaryData,
  ) => {
    setStoredProfile((current) => {
      if (current.requestedId !== requestedId) return current;
      if (current.load.status !== "ready" && current.load.status !== "partial") return current;

      const unavailable = new Set(current.load.data.unavailable);
      unavailable.delete(section);
      const data: MemberProfileData = {
        ...current.load.data,
        unavailable,
        ...(section === "relationships" ? { allMembers: value as Member[] } : {}),
        ...(section === "comments" ? { comments: value as Comment[] } : {}),
        ...(section === "albums" ? { albums: value as Album[] } : {}),
      };

      return {
        requestedId,
        load:
          unavailable.size === 0
            ? { status: "ready", data }
            : {
                status: "partial",
                data,
                problem:
                  current.load.status === "partial"
                    ? current.load.problem
                    : asApiProblem(undefined, "Some profile details could not be loaded."),
              },
      };
    });
  };

  const retryAuxiliary = (section: AuxiliarySection) => {
    const requestedId = id;
    const request = ++auxiliaryRequests.current[section];
    const read =
      section === "relationships"
        ? fetchMembers()
        : section === "comments"
          ? fetchComments(requestedId)
          : fetchAlbums(requestedId);

    read.then(
      (value) => {
        if (request !== auxiliaryRequests.current[section]) return;
        mergeAuxiliaryData(requestedId, section, value);
      },
      () => {
        // Retain the existing partial state and member draft for another local retry.
      },
    );
  };

  const addAlbum = (album: Album) => {
    setStoredProfile((current) => {
      if (current.requestedId !== id) return current;
      if (current.load.status !== "ready" && current.load.status !== "partial") return current;
      if (album.MemberRecordId && album.MemberRecordId !== current.load.data.member.id) {
        return current;
      }
      return {
        ...current,
        load: {
          ...current.load,
          data: {
            ...current.load.data,
            albums: [...current.load.data.albums, album],
          },
        },
      };
    });
  };

  const addComment = (comment: Comment) => {
    setStoredProfile((current) => {
      if (current.requestedId !== id) return current;
      if (current.load.status !== "ready" && current.load.status !== "partial") return current;
      if (comment.MemberRecordId !== current.load.data.member.id) return current;
      return {
        ...current,
        load: {
          ...current.load,
          data: {
            ...current.load.data,
            comments: [...current.load.data.comments, comment],
          },
        },
      };
    });
  };

  if (profileState.status === "loading") {
    return (
      <div className="min-h-[60vh]">
        <AsyncState state="loading" title="Loading member profile" />
      </div>
    );
  }

  if (profileState.status === "error") {
    return (
      <div className="mx-auto min-h-[60vh] max-w-2xl px-5 py-20">
        <AsyncState
          state="error"
          title="Member unavailable"
          message={profileState.problem.message}
          actionLabel="Retry"
          onAction={retryProfile}
        />
      </div>
    );
  }

  if (profileState.status === "empty") {
    return (
      <div className="mx-auto max-w-2xl px-5 py-20 text-center">
        <User className="w-12 h-12 mx-auto mb-4 text-text-light" />
        <h1 className="font-serif text-2xl font-bold mb-3">Member Not Found</h1>
        <Link href="/tree" className="text-accent hover:underline text-sm">
          &larr; Back to Family Tree
        </Link>
      </div>
    );
  }

  const { member, allMembers, comments, albums, unavailable } = profileState.data;

  const findMember = (recId: string | undefined) =>
    recId ? allMembers.find((m) => m.id === recId) : null;

  const father = findMember(member.FatherRecordId);
  const mother = findMember(member.MotherRecordId);
  const spouse = findMember(member.SpouseRecordId);
  const children = allMembers.filter(
    (m) => m.FatherRecordId === member.id || m.MotherRecordId === member.id
  );

  const genderAccent =
    member.Gender === "Male"
      ? "bg-sky-light text-sky"
      : member.Gender === "Female"
      ? "bg-plum-light text-plum"
      : "bg-bg-secondary text-text-muted";

  return (
    <div className="mx-auto max-w-3xl px-5 sm:px-8 py-10 sm:py-14 space-y-5">
      <Link
        href="/tree"
        className="inline-flex items-center gap-1.5 text-text-muted hover:text-accent text-sm mb-4 transition-heritage"
      >
        <ArrowLeft className="w-3.5 h-3.5" />
        Back to Tree
      </Link>

      {profileState.status === "partial" && (
        <div
          role="status"
          className="rounded-lg border border-terracotta-light bg-terracotta-light/20 px-4 py-3"
        >
          <p className="font-medium text-text-primary">Some profile details are unavailable</p>
          <p className="mt-1 text-sm text-text-muted">{profileState.problem.message}</p>
        </div>
      )}

      {/* Header */}
      <div className="heritage-card p-7 sm:p-8 animate-fadeInUp">
        <div className="flex flex-col sm:flex-row items-start gap-5">
          <div 
            className="w-20 h-20 sm:w-24 sm:h-24 rounded-2xl bg-accent/10 flex items-center justify-center text-3xl font-serif font-bold text-accent flex-shrink-0 bg-cover bg-center overflow-hidden border border-border"
            style={member.ProfileImageUrl ? { backgroundImage: `url(${member.ProfileImageUrl})` } : {}}
          >
            {!member.ProfileImageUrl && (member.FullName || "?")[0]}
          </div>
          <div className="flex-1">
            <h1 className="heading-serif text-2xl sm:text-3xl font-bold mb-2">
              {member.FullName || "Unknown"}
            </h1>
            <div className="flex flex-wrap items-center gap-3 text-sm">
              {member.Gender && (
                <span className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium ${genderAccent}`}>
                  {member.Gender}
                </span>
              )}
              {member.IsAlive ? (
                <span className="inline-flex items-center gap-1 text-emerald text-xs font-medium">
                  <Heart className="w-3 h-3 fill-current" />
                  Living
                </span>
              ) : (
                <span className="text-text-light text-xs">Deceased</span>
              )}
              {member.Generation && (
                <span className="text-xs text-text-muted bg-bg-secondary px-2.5 py-1 rounded-lg">
                  Generation {member.Generation}
                </span>
              )}
              {member.Branch && (
                <span className="text-xs text-accent bg-accent/6 px-2.5 py-1 rounded-lg">
                  {member.Branch}
                </span>
              )}
            </div>
          </div>
        </div>
      </div>

      <div className="grid md:grid-cols-2 gap-5">
        {/* Details */}
        <div className="heritage-card p-6">
          <h2 className="font-serif text-lg font-semibold mb-4 text-text-primary flex items-center gap-2">
            <Calendar className="w-4 h-4 text-accent" />
            Details
          </h2>
          <dl className="space-y-3.5">
            {[
              { label: "Date of Birth", value: member.DateOfBirth, icon: Calendar },
              { label: "Date of Death", value: member.DateOfDeath, icon: Calendar },
              { label: "City", value: member.CurrentCity, icon: MapPin },
              { label: "Country", value: member.CurrentCountry, icon: MapPin },
              { label: "Burial Location", value: member.BurialLocation, icon: MapPin },
            ]
              .filter((item) => item.value)
              .map((item) => (
                <div key={item.label} className="flex items-start gap-3">
                  <item.icon className="w-3.5 h-3.5 text-text-light mt-0.5 flex-shrink-0" />
                  <div>
                    <dt className="text-[11px] text-text-light uppercase tracking-wide">{item.label}</dt>
                    <dd className="text-sm font-medium text-text-primary">{item.value}</dd>
                  </div>
                </div>
              ))}
            {!member.DateOfBirth && !member.CurrentCity && (
              <p className="text-sm text-text-light italic">No details recorded yet.</p>
            )}
          </dl>
        </div>

        {/* Relationships */}
        <div className="heritage-card p-6">
          <h2 className="font-serif text-lg font-semibold mb-4 text-text-primary flex items-center gap-2">
            <Users className="w-4 h-4 text-accent" />
            Family
          </h2>
          <div className="space-y-2.5">
            {unavailable.has("relationships") ? (
              <SectionUnavailable
                title="Relationships unavailable"
                retryLabel="Retry relationships"
                onRetry={() => retryAuxiliary("relationships")}
              />
            ) : (
              <>
                {father && <RelationLink label="Father" member={father} />}
                {mother && <RelationLink label="Mother" member={mother} />}
                {spouse && <RelationLink label="Spouse" member={spouse} />}
                {children.length > 0 && (
                  <div className="pt-2">
                    <h3 className="text-[11px] text-text-light uppercase tracking-wide mb-2">
                      Children ({children.length})
                    </h3>
                    <div className="space-y-1.5">
                      {children.map((child) => (
                        <RelationLink key={child.id} member={child} />
                      ))}
                    </div>
                  </div>
                )}
                {!father && !mother && !spouse && children.length === 0 && (
                  <p className="text-sm italic text-text-light">No relationships recorded yet.</p>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {/* Biography */}
      {(member.Biography || member.Autobiography || member.HeritageStory) && (
        <div className="heritage-card p-6 animate-fadeInUp">
          <h2 className="font-serif text-lg font-semibold mb-3 text-text-primary flex items-center gap-2">
            <BookOpen className="w-4 h-4 text-accent" />
            Life Story
          </h2>
          <div className="space-y-6">
            {member.Biography && (
              <div>
                <h3 className="text-xs uppercase tracking-wider text-text-light font-bold mb-2">Biography</h3>
                <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">{member.Biography}</p>
              </div>
            )}
            {member.Autobiography && (
              <div className="bg-bg-secondary p-4 rounded-xl border border-border/50">
                <h3 className="text-xs uppercase tracking-wider text-accent font-bold mb-2 flex items-center gap-2">Autobiography</h3>
                <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap italic">{member.Autobiography}</p>
              </div>
            )}
            {member.HeritageStory && (
              <div>
                <h3 className="text-xs uppercase tracking-wider text-terracotta font-bold mb-2">Heritage Story</h3>
                <p className="text-sm text-text-secondary leading-relaxed whitespace-pre-wrap">{member.HeritageStory}</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Interactive Albums Section */}
      <AlbumSection
        key={member.id}
        member={member}
        albums={albums}
        unavailable={unavailable.has("albums")}
        onAlbumAdded={addAlbum}
        onRetry={() => retryAuxiliary("albums")}
      />

      {/* Comments Section */}
      <div className="heritage-card p-6 animate-fadeInUp">
        <h2 className="font-serif text-lg font-semibold mb-4 text-text-primary flex items-center gap-2">
          <MessageSquare className="w-5 h-5 text-accent" />
          Family Memories & Comments
        </h2>

        {/* List Comments */}
        <div className="space-y-4 mb-8">
          {unavailable.has("comments") ? (
            <SectionUnavailable
              title="Comments unavailable"
              retryLabel="Retry comments"
              onRetry={() => retryAuxiliary("comments")}
            />
          ) : comments.length === 0 ? (
            <p className="text-sm text-text-light italic">No comments yet. Be the first to share a memory.</p>
          ) : (
            comments.map((c) => (
              <div key={c.id} className="pe-4 py-3 border-b border-border last:border-0 relative">
                <div className="flex items-center gap-2 mb-1.5">
                  <div className="w-6 h-6 rounded-full bg-bg-secondary flex items-center justify-center text-xs font-bold text-text-muted">
                    {c.AuthorName[0].toUpperCase()}
                  </div>
                  <span className="text-sm font-semibold text-text-primary">{c.AuthorName}</span>
                  <span className="text-xs text-text-light flex-1">
                    {c.CreatedAt ? new Date(c.CreatedAt).toLocaleDateString() : ""}
                  </span>
                </div>
                <p className="text-sm text-text-secondary leading-relaxed pl-8">
                  {c.CommentText}
                </p>
              </div>
            ))
          )}
        </div>

        <CommentForm key={member.id} member={member} onCommentAdded={addComment} />
      </div>
    </div>
  );
}

function normalizeEmail(value: string): string {
  return value.trim().toLowerCase();
}

function CommentForm({
  member,
  onCommentAdded,
}: {
  member: Member;
  onCommentAdded: (comment: Comment) => void;
}) {
  const [commentText, setCommentText] = useState("");
  const [authorName, setAuthorName] = useState("");
  const [authorEmail, setAuthorEmail] = useState("");
  const [verifyingEmail, setVerifyingEmail] = useState(false);
  const [emailError, setEmailError] = useState("");
  const [verifiedEmail, setVerifiedEmail] = useState<string | null>(null);
  const [submittingComment, setSubmittingComment] = useState(false);
  const [commentError, setCommentError] = useState<string | null>(null);
  const authorEmailRef = useRef("");
  const verificationRequest = useRef(0);
  const commentRequest = useRef(0);

  useEffect(
    () => () => {
      verificationRequest.current += 1;
      commentRequest.current += 1;
    },
    [],
  );

  const handleEmailChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    const value = event.target.value;
    authorEmailRef.current = value;
    verificationRequest.current += 1;
    setAuthorEmail(value);
    setVerifiedEmail(null);
    setVerifyingEmail(false);
    setEmailError("");
  };

  const handleVerifyEmail = async () => {
    const requestedEmail = normalizeEmail(authorEmail);
    if (!requestedEmail) return;
    const request = ++verificationRequest.current;
    setVerifyingEmail(true);
    setEmailError("");
    try {
      const isApproved = await verifyEmail(requestedEmail);
      if (
        request !== verificationRequest.current ||
        normalizeEmail(authorEmailRef.current) !== requestedEmail
      ) {
        return;
      }
      if (isApproved) {
        setVerifiedEmail(requestedEmail);
      } else {
        setVerifiedEmail(null);
        setEmailError("This email is not on the approved family members list.");
      }
    } catch (error: unknown) {
      if (request !== verificationRequest.current) return;
      setEmailError(asApiProblem(error, "Email verification could not be completed.").message);
    } finally {
      if (request === verificationRequest.current) {
        setVerifyingEmail(false);
      }
    }
  };

  const isEmailVerified =
    verifiedEmail !== null && verifiedEmail === normalizeEmail(authorEmail);

  const submitComment = async () => {
    if (!commentText || !authorName || !isEmailVerified || !verifiedEmail) return;
    const request = ++commentRequest.current;
    setSubmittingComment(true);
    setCommentError(null);
    try {
      const newComment = await postComment({
        MemberRecordId: member.id,
        MemberName: member.FullName,
        AuthorName: authorName,
        AuthorEmail: verifiedEmail,
        CommentText: commentText,
      });
      if (request !== commentRequest.current) return;
      onCommentAdded(newComment);
      setCommentText("");
    } catch (error: unknown) {
      if (request !== commentRequest.current) return;
      setCommentError(asApiProblem(error, "The comment could not be posted.").message);
    } finally {
      if (request === commentRequest.current) {
        setSubmittingComment(false);
      }
    }
  };

  return (
    <div className="bg-bg-secondary p-5 rounded-xl border border-border shadow-inner">
      <h3 className="font-serif font-semibold text-text-primary mb-2 flex items-center gap-2">
        Leave a memory
      </h3>
      <p className="text-xs text-text-muted mb-4 opacity-80">
        For privacy, only verified family members can leave comments.
      </p>

      {!isEmailVerified ? (
        <div className="flex flex-col gap-3">
          <div>
            <p className="text-xs font-medium text-text-primary mb-1">Verify your family email</p>
            <div className="flex gap-2">
              <input
                type="email"
                aria-label="Family email"
                value={authorEmail}
                onChange={handleEmailChange}
                placeholder="name@example.com"
                className="flex-1 px-3 py-2 rounded-lg border border-border text-sm outline-none focus:border-accent"
              />
              <button
                type="button"
                onClick={handleVerifyEmail}
                disabled={verifyingEmail || !normalizeEmail(authorEmail)}
                className="btn-primary"
              >
                {verifyingEmail ? (
                  <Loader2 className="w-4 h-4 animate-spin" />
                ) : (
                  <Lock className="w-4 h-4" />
                )}
                Verify
              </button>
            </div>
            {emailError && (
              <p role="alert" className="text-terracotta text-xs mt-1.5">
                {emailError}
              </p>
            )}
          </div>
        </div>
      ) : (
        <div className="space-y-3 animate-fadeInUp">
          <div className="flex items-center gap-2 bg-emerald-light/30 border border-emerald/20 text-emerald-dark px-3 py-2 rounded-lg text-xs font-medium mb-2">
            Email verified. You can now post comments.
          </div>
          <input
            type="text"
            aria-label="Your name"
            value={authorName}
            onChange={(event) => setAuthorName(event.target.value)}
            placeholder="Your Name"
            className="w-full px-3 py-2 rounded-lg border border-border text-sm outline-none focus:border-accent"
          />
          <textarea
            aria-label="Memory or story"
            value={commentText}
            onChange={(event) => setCommentText(event.target.value)}
            placeholder="Share a memory or story..."
            rows={3}
            className="w-full px-3 py-2 rounded-lg border border-border text-sm outline-none focus:border-accent resize-y"
          />
          <div className="flex justify-end">
            {commentError && (
              <p role="alert" className="mr-auto self-center text-xs text-terracotta">
                {commentError}
              </p>
            )}
            <button
              type="button"
              onClick={submitComment}
              disabled={submittingComment || !commentText || !authorName}
              className="btn-primary"
            >
              {submittingComment ? (
                <Loader2 className="w-4 h-4 animate-spin" />
              ) : (
                <Send className="w-4 h-4" />
              )}
              Post Comment
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function RelationLink({ label, member }: { label?: string; member: Member }) {
  return (
    <Link
      href={`/member/${member.id}`}
      className="flex items-center gap-2.5 px-3 py-2.5 rounded-lg bg-bg-primary border border-transparent hover:border-border hover:shadow-sm transition-heritage group"
    >
      <div className="w-8 h-8 rounded-full bg-accent/8 flex items-center justify-center text-xs font-serif font-bold text-accent flex-shrink-0 group-hover:bg-accent group-hover:text-white transition-heritage">
        {(member.FullName || "?")[0]}
      </div>
      <div className="flex-1 min-w-0">
        {label && <span className="text-[11px] text-text-light uppercase tracking-wide">{label}</span>}
        <div className="text-sm font-medium text-text-primary truncate">{member.FullName}</div>
      </div>
      <ExternalLink className="w-3 h-3 text-text-light opacity-0 group-hover:opacity-100 transition-heritage" />
    </Link>
  );
}

function SectionUnavailable({
  title,
  retryLabel,
  onRetry,
}: {
  title: string;
  retryLabel: string;
  onRetry: () => void;
}) {
  return (
    <div className="rounded-lg border border-terracotta-light bg-terracotta-light/20 p-3">
      <p className="text-sm font-medium text-text-primary">{title}</p>
      <button
        type="button"
        onClick={onRetry}
        className="mt-2 text-xs font-medium text-accent underline-offset-2 hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        {retryLabel}
      </button>
    </div>
  );
}

function albumImageUrl(image: Album["ImageUrl"]): string | null {
  if (typeof image === "string") return image;
  if (Array.isArray(image) && typeof image[0]?.url === "string") return image[0].url;
  return null;
}

type AlbumSectionProps = {
  member: Member;
  albums: Album[];
  unavailable: boolean;
  onAlbumAdded: (album: Album) => void;
  onRetry: () => void;
};

function AlbumSection({
  member,
  albums,
  unavailable,
  onAlbumAdded,
  onRetry,
}: AlbumSectionProps) {
  const [showForm, setShowForm] = useState(false);
  const [caption, setCaption] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadedUrl, setUploadedUrl] = useState("");
  const [albumError, setAlbumError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const albumRequest = useRef(0);

  useEffect(
    () => () => {
      albumRequest.current += 1;
    },
    [],
  );

  const uploadPhoto = async (file: File) => {
    const request = ++albumRequest.current;
    setUploading(true);
    setAlbumError(null);
    try {
      const data = await uploadImage(file);
      if (request !== albumRequest.current) return;
      setUploadedUrl(data.url);
    } catch (error: unknown) {
      if (request !== albumRequest.current) return;
      setAlbumError(asApiProblem(error, "The photo could not be uploaded.").message);
    } finally {
      if (request === albumRequest.current) {
        setUploading(false);
      }
    }
  };

  const handlePhotoSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setSelectedFile(file);
    event.target.value = "";
    void uploadPhoto(file);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!uploadedUrl) return;
    const request = ++albumRequest.current;
    setUploading(true);
    setAlbumError(null);
    try {
      const newAlbum = await uploadAlbumPhoto({
        MemberRecordId: member.id,
        MemberName: member.FullName,
        ImageUrl: uploadedUrl,
        Caption: caption,
      });
      if (request !== albumRequest.current) return;
      onAlbumAdded(newAlbum);
      setShowForm(false);
      setUploadedUrl("");
      setCaption("");
      setSelectedFile(null);
    } catch (error: unknown) {
      if (request !== albumRequest.current) return;
      setAlbumError(asApiProblem(error, "The photo could not be added to the album.").message);
    } finally {
      if (request === albumRequest.current) {
        setUploading(false);
      }
    }
  };

  return (
    <div className="heritage-card p-6 animate-fadeInUp">
      <div className="flex justify-between items-center mb-4">
        <h2 className="font-serif text-lg font-semibold text-text-primary flex items-center gap-2">
          <Camera className="w-5 h-5 text-accent" />
          Photo Albums
        </h2>
        {!unavailable && (
          <button type="button" onClick={() => setShowForm(!showForm)} className="text-accent text-sm font-medium hover:underline flex items-center gap-1 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent">
            <Plus className="w-4 h-4" /> Add Photo
          </button>
        )}
      </div>

      {unavailable ? (
        <SectionUnavailable
          title="Albums unavailable"
          retryLabel="Retry albums"
          onRetry={onRetry}
        />
      ) : showForm && (
        <form onSubmit={handleSubmit} className="mb-6 p-4 bg-bg-secondary rounded-lg border border-border">
          <p className="text-xs text-text-muted mb-4 leading-relaxed">
            Select a photo from your device to upload it to the album.
          </p>
          <div className="space-y-3">
            {uploadedUrl ? (
              <div className="flex items-center gap-4 p-3 border border-border rounded-lg bg-bg-primary">
                <div
                  role="img"
                  aria-label="Album photo preview"
                  className="h-16 w-16 rounded-lg bg-cover bg-center"
                  style={{ backgroundImage: `url(${uploadedUrl})` }}
                />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-text-primary">Photo ready</p>
                  <button
                    type="button"
                    onClick={() => {
                      setUploadedUrl("");
                      setSelectedFile(null);
                    }}
                    className="text-xs text-terracotta hover:underline"
                  >
                    Remove &amp; choose another
                  </button>
                </div>
              </div>
            ) : (
              <div className="relative">
                <input aria-label="Choose album photo" type="file" accept="image/*" onChange={handlePhotoSelect} disabled={uploading} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed focus-visible:opacity-100" />
                <div className={`w-full px-4 py-3 rounded-lg border border-border bg-bg-primary flex items-center justify-center gap-2 transition-all ${uploading ? 'opacity-50' : 'hover:border-accent hover:text-accent'}`}>
                  {uploading ? <Loader2 className="w-4 h-4 animate-spin text-accent" /> : <Camera className="w-4 h-4 text-text-muted" />}
                  <span className="text-sm font-medium text-text-muted">{uploading ? "Uploading..." : "Click to select a photo"}</span>
                </div>
              </div>
            )}
            <div>
              <input 
                type="text" placeholder="Caption (optional)" 
                aria-label="Photo caption"
                value={caption} onChange={(e) => setCaption(e.target.value)} 
                className="input-heritage w-full"
              />
            </div>
            {albumError && <p role="alert" className="text-sm text-terracotta">{albumError}</p>}
            {!uploadedUrl && selectedFile && albumError && (
              <button
                type="button"
                onClick={() => void uploadPhoto(selectedFile)}
                disabled={uploading}
                className="btn-secondary w-full"
              >
                Retry Upload
              </button>
            )}
            <button type="submit" disabled={uploading || !uploadedUrl} className="btn-primary w-full">
              {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Add to Album"}
            </button>
          </div>
        </form>
      )}

      {/* Legacy and New Photos merged */}
      {!unavailable && <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {member.Photos?.map((photo) => (
          <a key={photo.url} href={photo.url} target="_blank" rel="noopener noreferrer" aria-label="Open legacy photo" className="block rounded-lg overflow-hidden border border-border group relative">
            <span role="img" aria-label="Legacy photo" className="block h-36 w-full bg-cover bg-center transition-transform group-hover:scale-105" style={{ backgroundImage: `url(${photo.url})` }} />
          </a>
        ))}
        {albums.map((al) => {
          const imgUrl = albumImageUrl(al.ImageUrl);
          if (!imgUrl) return null;
          return (
            <a key={al.id} href={imgUrl} target="_blank" rel="noopener noreferrer" aria-label={`Open ${al.Caption || "album photo"}`} className="block rounded-lg overflow-hidden border border-border group relative">
              <span role="img" aria-label={al.Caption || "Album photo"} className="block h-36 w-full bg-cover bg-center transition-transform group-hover:scale-105" style={{ backgroundImage: `url(${imgUrl})` }} />
              {al.Caption && (
                <div className="absolute bottom-0 inset-x-0 bg-black/60 p-2 text-xs text-white truncate backdrop-blur-sm">
                  {al.Caption}
                </div>
              )}
            </a>
          );
        })}
        {!member.Photos?.length && albums.length === 0 && !showForm && (
          <p className="text-sm text-text-light italic col-span-full">No photos added yet.</p>
        )}
      </div>}
    </div>
  );
}

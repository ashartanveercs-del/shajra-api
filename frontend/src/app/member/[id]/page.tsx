"use client";

import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import Link from "next/link";
import { fetchMember, fetchMembers, fetchComments, postComment, verifyEmail, fetchAlbums, uploadAlbumPhoto, type Member, type Comment, type Album } from "@/lib/api";
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
  Mail,
  Phone,
  Camera,
  Plus
} from "lucide-react";

export default function MemberProfilePage() {
  const params = useParams();
  const id = params.id as string;
  const [member, setMember] = useState<Member | null>(null);
  const [allMembers, setAllMembers] = useState<Member[]>([]);
  const [comments, setComments] = useState<Comment[]>([]);
  const [albums, setAlbums] = useState<Album[]>([]);
  const [loading, setLoading] = useState(true);

  // Comment form state
  const [commentText, setCommentText] = useState("");
  const [authorName, setAuthorName] = useState("");
  const [authorEmail, setAuthorEmail] = useState("");
  const [verifyingEmail, setVerifyingEmail] = useState(false);
  const [emailError, setEmailError] = useState("");
  const [isEmailVerified, setIsEmailVerified] = useState(false);
  const [submittingComment, setSubmittingComment] = useState(false);

  useEffect(() => {
      Promise.all([fetchMember(id), fetchMembers(), fetchComments(id), fetchAlbums(id)])
      .then(([m, all, fetchedComments, fetchedAlbums]) => {
        setMember(m);
        setAllMembers(all);
        setComments(fetchedComments);
        setAlbums(fetchedAlbums);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [id]);

  const handleVerifyEmail = async () => {
    if (!authorEmail) return;
    setVerifyingEmail(true);
    setEmailError("");
    try {
      const isApproved = await verifyEmail(authorEmail);
      if (isApproved) {
        setIsEmailVerified(true);
      } else {
        setEmailError("This email is not on the approved family members list.");
      }
    } catch {
      setEmailError("Verification failed.");
    } finally {
      setVerifyingEmail(false);
    }
  };

  const submitComment = async () => {
    if (!commentText || !authorName || !isEmailVerified || !member) return;
    setSubmittingComment(true);
    try {
      const newComment = await postComment({
        MemberRecordId: member.id,
        MemberName: member.FullName,
        AuthorName: authorName,
        AuthorEmail: authorEmail,
        CommentText: commentText,
      });
      setComments([...comments, newComment]);
      setCommentText("");
    } catch (e: any) {
      alert(e.message || "Failed to post comment");
    } finally {
      setSubmittingComment(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <Loader2 className="w-6 h-6 animate-spin text-accent" />
      </div>
    );
  }

  if (!member) {
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
              { label: "Email", value: member.Email, icon: Mail },
              { label: "Phone", value: member.PhoneNumber, icon: Phone },
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
      <AlbumSection member={member} albums={albums} setAlbums={setAlbums} />

      {/* Comments Section */}
      <div className="heritage-card p-6 animate-fadeInUp">
        <h2 className="font-serif text-lg font-semibold mb-4 text-text-primary flex items-center gap-2">
          <MessageSquare className="w-5 h-5 text-accent" />
          Family Memories & Comments
        </h2>

        {/* List Comments */}
        <div className="space-y-4 mb-8">
          {comments.length === 0 ? (
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

        {/* Comment Form */}
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
                    value={authorEmail}
                    onChange={(e) => setAuthorEmail(e.target.value)}
                    placeholder="name@example.com"
                    className="flex-1 px-3 py-2 rounded-lg border border-border text-sm outline-none focus:border-accent"
                  />
                  <button
                    onClick={handleVerifyEmail}
                    disabled={verifyingEmail || !authorEmail}
                    className="btn-primary"
                  >
                    {verifyingEmail ? <Loader2 className="w-4 h-4 animate-spin" /> : <Lock className="w-4 h-4" />}
                    Verify
                  </button>
                </div>
                {emailError && <p className="text-terracotta text-xs mt-1.5">{emailError}</p>}
              </div>
            </div>
          ) : (
            <div className="space-y-3 animate-fadeInUp">
              <div className="flex items-center gap-2 bg-emerald-light/30 border border-emerald/20 text-emerald-dark px-3 py-2 rounded-lg text-xs font-medium mb-2">
                Email verified. You can now post comments.
              </div>
              <input
                type="text"
                value={authorName}
                onChange={(e) => setAuthorName(e.target.value)}
                placeholder="Your Name"
                className="w-full px-3 py-2 rounded-lg border border-border text-sm outline-none focus:border-accent"
              />
              <textarea
                value={commentText}
                onChange={(e) => setCommentText(e.target.value)}
                placeholder="Share a memory or story..."
                rows={3}
                className="w-full px-3 py-2 rounded-lg border border-border text-sm outline-none focus:border-accent resize-y"
              />
              <div className="flex justify-end">
                <button
                  onClick={submitComment}
                  disabled={submittingComment || !commentText || !authorName}
                  className="btn-primary"
                >
                  {submittingComment ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                  Post Comment
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
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

function AlbumSection({ member, albums, setAlbums }: { member: Member; albums: Album[]; setAlbums: any }) {
  const [showForm, setShowForm] = useState(false);
  const [caption, setCaption] = useState("");
  const [uploading, setUploading] = useState(false);
  const [uploadedUrl, setUploadedUrl] = useState("");

  const handlePhotoSelect = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    try {
      const { uploadImage } = await import("@/lib/api");
      const data = await uploadImage(file);
      setUploadedUrl(data.url);
    } catch (err: any) {
      alert(err.message || "Failed to upload image.");
    } finally {
      setUploading(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!uploadedUrl) return;
    setUploading(true);
    try {
      const newAlbum = await uploadAlbumPhoto({
        MemberRecordId: member.id,
        MemberName: member.FullName,
        ImageUrl: uploadedUrl,
        Caption: caption,
      });
      setAlbums([...albums, newAlbum]);
      setShowForm(false);
      setUploadedUrl("");
      setCaption("");
    } catch (err: any) {
      alert("Failed to upload: " + err.message);
    }
    setUploading(false);
  };

  return (
    <div className="heritage-card p-6 animate-fadeInUp">
      <div className="flex justify-between items-center mb-4">
        <h2 className="font-serif text-lg font-semibold text-text-primary flex items-center gap-2">
          <Camera className="w-5 h-5 text-accent" />
          Photo Albums
        </h2>
        <button onClick={() => setShowForm(!showForm)} className="text-accent text-sm font-medium hover:underline flex items-center gap-1">
          <Plus className="w-4 h-4" /> Add Photo
        </button>
      </div>

      {showForm && (
        <form onSubmit={handleSubmit} className="mb-6 p-4 bg-bg-secondary rounded-xl border border-border">
          <p className="text-xs text-text-muted mb-4 leading-relaxed">
            Select a photo from your device to upload it to the album.
          </p>
          <div className="space-y-3">
            {uploadedUrl ? (
              <div className="flex items-center gap-4 p-3 border border-border rounded-lg bg-bg-primary">
                <img src={uploadedUrl} alt="Preview" className="w-16 h-16 rounded-lg object-cover" />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-text-primary">Photo ready</p>
                  <button type="button" onClick={() => setUploadedUrl("")} className="text-xs text-terracotta hover:underline">Remove & choose another</button>
                </div>
              </div>
            ) : (
              <div className="relative">
                <input type="file" accept="image/*" onChange={handlePhotoSelect} disabled={uploading} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed" />
                <div className={`w-full px-4 py-3 rounded-lg border border-border bg-bg-primary flex items-center justify-center gap-2 transition-all ${uploading ? 'opacity-50' : 'hover:border-accent hover:text-accent'}`}>
                  {uploading ? <Loader2 className="w-4 h-4 animate-spin text-accent" /> : <Camera className="w-4 h-4 text-text-muted" />}
                  <span className="text-sm font-medium text-text-muted">{uploading ? "Uploading..." : "Click to select a photo"}</span>
                </div>
              </div>
            )}
            <div>
              <input 
                type="text" placeholder="Caption (optional)" 
                value={caption} onChange={(e) => setCaption(e.target.value)} 
                className="input-heritage w-full"
              />
            </div>
            <button disabled={uploading || !uploadedUrl} className="btn-primary w-full">
              {uploading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Add to Album"}
            </button>
          </div>
        </form>
      )}

      {/* Legacy and New Photos merged */}
      <div className="grid grid-cols-2 sm:grid-cols-3 gap-3">
        {member.Photos?.map((photo, i) => (
          <a key={`legacy-${i}`} href={photo.url} target="_blank" rel="noopener noreferrer" className="block rounded-xl overflow-hidden border border-border group relative">
            <img src={photo.url} alt="Legacy Photo" className="w-full h-36 object-cover group-hover:scale-105 transition-transform" />
          </a>
        ))}
        {albums.map((al) => {
          const imgUrl = typeof al.ImageUrl === 'string' ? al.ImageUrl : (al.ImageUrl as any)?.[0]?.url;
          if (!imgUrl) return null;
          return (
            <a key={al.id} href={imgUrl} target="_blank" rel="noopener noreferrer" className="block rounded-xl overflow-hidden border border-border group relative">
              <img src={imgUrl} alt={al.Caption || 'Album Photo'} className="w-full h-36 object-cover group-hover:scale-105 transition-transform" />
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
      </div>
    </div>
  );
}

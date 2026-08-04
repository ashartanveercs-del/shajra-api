"use client";

import { useState } from "react";
import { submitDirectForm, uploadImage } from "@/lib/api";
import { asApiProblem } from "@/lib/loadable";
import { ArrowRight, CheckCircle2, Loader2, Info } from "lucide-react";
import Link from "next/link";

const EMPTY_FORM = {
  fullName: "",
  fatherName: "",
  motherName: "",
  spouseName: "",
  dateOfBirth: "",
  dateOfDeath: "",
  location: "",
  burialLocation: "",
  gender: "",
  biography: "",
  email: "",
  phoneNumber: "",
  profileImage: "",
};

export default function SubmitPage() {
  const [formData, setFormData] = useState(EMPTY_FORM);

  const [loading, setLoading] = useState(false);
  const [uploadingImage, setUploadingImage] = useState(false);
  const [success, setSuccess] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [selectedPhotoFile, setSelectedPhotoFile] = useState<File | null>(null);

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    setFormData((current) => ({ ...current, [e.target.name]: e.target.value }));
  };

  const uploadPhoto = async (file: File) => {
    setUploadingImage(true);
    setUploadError(null);
    try {
      const data = await uploadImage(file);
      setFormData((current) => ({ ...current, profileImage: data.url }));
    } catch (error: unknown) {
      setUploadError(asApiProblem(error, "The profile photo could not be uploaded.").message);
    } finally {
      setUploadingImage(false);
    }
  };

  const handlePhotoUpload = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    setSelectedPhotoFile(file);
    event.target.value = "";
    void uploadPhoto(file);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setSubmitError(null);

    try {
      if (!formData.fullName) {
        setSubmitError("Full Name is required.");
        return;
      }
      await submitDirectForm(formData);
      setSuccess(true);
    } catch (error: unknown) {
      setSubmitError(asApiProblem(error, "The submission could not be sent.").message);
    } finally {
      setLoading(false);
    }
  };

  if (success) {
    return (
      <div className="mx-auto max-w-2xl px-5 py-24 text-center animate-fadeInUp">
        <div className="w-16 h-16 mx-auto bg-emerald/10 text-emerald rounded-full flex items-center justify-center mb-6">
          <CheckCircle2 className="w-8 h-8" />
        </div>
        <h1 className="heading-serif text-3xl font-bold mb-4">Submission Received</h1>
        <p className="text-text-secondary leading-relaxed mb-8">
          Thank you for contributing to your family heritage. An administrator will review the details before they appear in the archive.
        </p>
        <div className="flex justify-center gap-4">
          <Link href="/tree" className="btn-primary">
            Back to Tree
          </Link>
          <button type="button" onClick={() => { setSuccess(false); setFormData(EMPTY_FORM); }} className="btn-secondary">
            Submit Another
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-5 sm:px-8 py-12 sm:py-16">
      <div className="mb-8 animate-fadeInUp">
        <p className="text-accent text-sm font-medium uppercase tracking-wide mb-2 flex items-center gap-2">
          <span className="w-6 h-px bg-accent" />
          Contribute
        </p>
        <h1 className="heading-serif text-3xl sm:text-4xl font-bold mb-3">
          Add a Family Member
        </h1>
        <p className="text-text-muted text-base">
          Fill in as much detail as you remember for the family administrator to review.
        </p>
      </div>

      <div className="bg-sky-light/50 border border-sky/20 rounded-lg p-4 flex gap-3 text-sm text-sky-900 mb-8">
        <Info className="w-5 h-5 flex-shrink-0 text-sky mt-0.5" />
        <p>If you don&apos;t know an exact date, just the year (e.g., &quot;1960&quot;) is fine. For locations, please include both City and Country if known.</p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6 heritage-card p-6 sm:p-8 animate-fadeInUp" style={{ animationDelay: "100ms" }}>
        
        {uploadError && (
          <div role="alert" aria-label="Photo upload failed" className="bg-terracotta-light/30 border border-terracotta-light text-terracotta text-sm p-4 rounded-lg">
            <p>{uploadError}</p>
            {selectedPhotoFile && (
              <button
                type="button"
                onClick={() => void uploadPhoto(selectedPhotoFile)}
                disabled={uploadingImage}
                className="btn-secondary mt-3"
              >
                Retry Upload
              </button>
            )}
          </div>
        )}

        {submitError && (
          <div role="alert" aria-label="Submission failed" className="bg-terracotta-light/30 border border-terracotta-light text-terracotta text-sm p-4 rounded-lg">
            {submitError}
          </div>
        )}

        <div className="space-y-4">
          <h3 className="font-serif font-semibold text-lg border-b border-border pb-2">Essential Details</h3>
          
          <div className="grid sm:grid-cols-2 gap-4">
            <div className="space-y-1.5 sm:col-span-2">
              <label htmlFor="submit-full-name" className="text-sm font-medium text-text-primary">Full Name <span className="text-terracotta">*</span></label>
              <input id="submit-full-name" type="text" name="fullName" value={formData.fullName} onChange={handleChange} required className="w-full px-4 py-2.5 rounded-lg border border-border bg-bg-primary focus:border-accent focus:ring-1 focus:ring-accent outline-none transition-all" placeholder="E.g., Muhammad Ali" />
            </div>
            
            <div className="space-y-1.5">
              <label htmlFor="submit-gender" className="text-sm font-medium text-text-primary">Gender</label>
              <select id="submit-gender" name="gender" value={formData.gender} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border bg-bg-primary focus:border-accent outline-none">
                <option value="">Select...</option>
                <option value="Male">Male</option>
                <option value="Female">Female</option>
              </select>
            </div>
          </div>
          
          <div className="grid sm:grid-cols-2 gap-4 mt-2">
            <div className="space-y-1.5">
              <label htmlFor="submit-email" className="text-sm font-medium text-text-primary">Email Address</label>
              <input id="submit-email" type="email" name="email" value={formData.email} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border bg-bg-primary focus:border-accent outline-none" placeholder="name@example.com" />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="submit-phone" className="text-sm font-medium text-text-primary">Phone Number</label>
              <input id="submit-phone" type="tel" name="phoneNumber" value={formData.phoneNumber} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border bg-bg-primary focus:border-accent outline-none" placeholder="+1..." />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <label htmlFor="submit-profile-picture" className="text-sm font-medium text-text-primary">Profile Picture</label>
              {formData.profileImage ? (
                <div className="flex items-center gap-4 p-3 border border-border rounded-lg bg-bg-primary">
                  <div role="img" aria-label="Profile preview" className="h-12 w-12 rounded-full bg-cover bg-center" style={{ backgroundImage: `url(${formData.profileImage})` }} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-text-primary truncate">Image Uploaded</p>
                    <button
                      type="button"
                      onClick={() => {
                        setFormData({ ...formData, profileImage: "" });
                        setSelectedPhotoFile(null);
                      }}
                      className="text-xs text-terracotta hover:underline"
                    >
                      Remove
                    </button>
                  </div>
                </div>
              ) : (
                <div className="relative">
                  <input id="submit-profile-picture" aria-label="Profile picture" type="file" accept="image/*" onChange={handlePhotoUpload} disabled={uploadingImage} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed focus-visible:opacity-100" />
                  <div className={`w-full px-4 py-2.5 rounded-lg border border-border bg-bg-primary flex items-center justify-center gap-2 transition-all ${uploadingImage ? 'opacity-50' : 'hover:border-accent hover:text-accent'}`}>
                    {uploadingImage ? <Loader2 className="w-4 h-4 animate-spin text-accent" /> : <Loader2 className="w-4 h-4 opacity-0 hidden" />}
                    <span className="text-sm font-medium text-text-muted">{uploadingImage ? "Uploading..." : "Click to select a photo"}</span>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>

        <div className="space-y-4 pt-2">
          <h3 className="font-serif font-semibold text-lg border-b border-border pb-2">Family Connections</h3>
          <div className="grid sm:grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label htmlFor="submit-father" className="text-sm font-medium text-text-primary">Father&apos;s Full Name</label>
              <input id="submit-father" type="text" name="fatherName" value={formData.fatherName} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="submit-mother" className="text-sm font-medium text-text-primary">Mother&apos;s Full Name</label>
              <input id="submit-mother" type="text" name="motherName" value={formData.motherName} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <label htmlFor="submit-spouse" className="text-sm font-medium text-text-primary">Spouse&apos;s Full Name (if applicable)</label>
              <input id="submit-spouse" type="text" name="spouseName" value={formData.spouseName} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
          </div>
        </div>

        <div className="space-y-4 pt-2">
          <h3 className="font-serif font-semibold text-lg border-b border-border pb-2">Dates & Places</h3>
          <div className="grid sm:grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label htmlFor="submit-birth" className="text-sm font-medium text-text-primary">Date of Birth</label>
              <input id="submit-birth" type="text" name="dateOfBirth" placeholder="DD-MM-YYYY or YYYY" value={formData.dateOfBirth} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="submit-location" className="text-sm font-medium text-text-primary">Current Residence (City, Country)</label>
              <input id="submit-location" type="text" name="location" placeholder="E.g., Lahore, Pakistan" value={formData.location} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="submit-death" className="text-sm font-medium text-text-primary">Date of Death (if deceased)</label>
              <input id="submit-death" type="text" name="dateOfDeath" placeholder="DD-MM-YYYY or YYYY" value={formData.dateOfDeath} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
            <div className="space-y-1.5">
              <label htmlFor="submit-burial" className="text-sm font-medium text-text-primary">Burial Location</label>
              <input id="submit-burial" type="text" name="burialLocation" placeholder="Cemetery, City" value={formData.burialLocation} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
          </div>
        </div>

        <div className="space-y-4 pt-2">
          <h3 className="font-serif font-semibold text-lg border-b border-border pb-2">Biography</h3>
          <div className="space-y-1.5">
            <label htmlFor="submit-biography" className="text-sm font-medium text-text-primary">Life Story or Notes</label>
            <textarea id="submit-biography" name="biography" value={formData.biography} onChange={handleChange} rows={4} className="w-full px-4 py-2.5 rounded-lg border border-border resize-y bg-bg-primary focus:border-accent" placeholder="Share any memories, profession, or interesting facts..." />
          </div>
        </div>

        <div className="pt-4 flex justify-end">
          <button type="submit" disabled={loading || uploadingImage} className="btn-primary w-full sm:w-auto mt-4 px-8 py-3 bg-accent text-white flex justify-center items-center">
            {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : (
              <>
                Submit to Family Archive
                <ArrowRight className="w-4 h-4 ml-2" />
              </>
            )}
          </button>
        </div>
      </form>
    </div>
  );
}

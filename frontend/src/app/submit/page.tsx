"use client";

import { useState } from "react";
import { submitDirectForm, uploadImage } from "@/lib/api";
import { ArrowRight, CheckCircle2, Loader2, Info } from "lucide-react";
import Link from "next/link";

export default function SubmitPage() {
  const [formData, setFormData] = useState({
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
  });
  
  const [loading, setLoading] = useState(false);
  const [uploadingImage, setUploadingImage] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState("");

  const handleChange = (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => {
    setFormData({ ...formData, [e.target.name]: e.target.value });
  };

  const handlePhotoUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    
    setUploadingImage(true);
    setError("");
    try {
      const data = await uploadImage(file);
      setFormData({ ...formData, profileImage: data.url });
    } catch (err: any) {
      setError(err.message || "Failed to upload image. Please try again.");
    } finally {
      setUploadingImage(false);
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError("");
    
    try {
      if (!formData.fullName) {
        throw new Error("Full Name is required");
      }
      await submitDirectForm(formData);
      setSuccess(true);
    } catch (err: any) {
      setError(err.message || "Failed to submit form. Please try again.");
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
          Thank you for contributing to your family heritage. Our AI is currently processing the details and finding any connections. It will be reviewed by an administrator shortly.
        </p>
        <div className="flex justify-center gap-4">
          <Link href="/tree" className="btn-primary">
            Back to Tree
          </Link>
          <button onClick={() => { setSuccess(false); setFormData({} as any); }} className="btn-secondary">
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
          Fill in as much detail as you remember. Our AI will help match them to the correct family branch.
        </p>
      </div>

      <div className="bg-sky-light/50 border border-sky/20 rounded-xl p-4 flex gap-3 text-sm text-sky-900 mb-8">
        <Info className="w-5 h-5 flex-shrink-0 text-sky mt-0.5" />
        <p>If you don't know an exact date, just the year (e.g., "1960") is fine. For locations, please include both City and Country if known.</p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-6 heritage-card p-6 sm:p-8 animate-fadeInUp" style={{ animationDelay: "100ms" }}>
        
        {error && (
          <div className="bg-terracotta-light/30 border border-terracotta-light text-terracotta text-sm p-4 rounded-xl">
            {error}
          </div>
        )}

        <div className="space-y-4">
          <h3 className="font-serif font-semibold text-lg border-b border-border pb-2">Essential Details</h3>
          
          <div className="grid sm:grid-cols-2 gap-4">
            <div className="space-y-1.5 sm:col-span-2">
              <label className="text-sm font-medium text-text-primary">Full Name <span className="text-terracotta">*</span></label>
              <input type="text" name="fullName" value={formData.fullName} onChange={handleChange} required className="w-full px-4 py-2.5 rounded-lg border border-border bg-bg-primary focus:border-accent focus:ring-1 focus:ring-accent outline-none transition-all" placeholder="E.g., Muhammad Ali" />
            </div>
            
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-text-primary">Gender</label>
              <select name="gender" value={formData.gender} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border bg-bg-primary focus:border-accent outline-none">
                <option value="">Select...</option>
                <option value="Male">Male</option>
                <option value="Female">Female</option>
              </select>
            </div>
          </div>
          
          <div className="grid sm:grid-cols-2 gap-4 mt-2">
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-text-primary">Email Address</label>
              <input type="email" name="email" value={formData.email} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border bg-bg-primary focus:border-accent outline-none" placeholder="name@example.com" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-text-primary">Phone Number</label>
              <input type="tel" name="phoneNumber" value={formData.phoneNumber} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border bg-bg-primary focus:border-accent outline-none" placeholder="+1..." />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <label className="text-sm font-medium text-text-primary">Profile Picture</label>
              {formData.profileImage ? (
                <div className="flex items-center gap-4 p-3 border border-border rounded-lg bg-bg-primary">
                  <img src={formData.profileImage} alt="Profile preview" className="w-12 h-12 rounded-full object-cover" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-text-primary truncate">Image Uploaded</p>
                    <button type="button" onClick={() => setFormData({ ...formData, profileImage: "" })} className="text-xs text-terracotta hover:underline">Remove</button>
                  </div>
                </div>
              ) : (
                <div className="relative">
                  <input type="file" accept="image/*" onChange={handlePhotoUpload} disabled={uploadingImage} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer disabled:cursor-not-allowed" />
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
              <label className="text-sm font-medium text-text-primary">Father's Full Name</label>
              <input type="text" name="fatherName" value={formData.fatherName} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-text-primary">Mother's Full Name</label>
              <input type="text" name="motherName" value={formData.motherName} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
            <div className="space-y-1.5 sm:col-span-2">
              <label className="text-sm font-medium text-text-primary">Spouse's Full Name (if applicable)</label>
              <input type="text" name="spouseName" value={formData.spouseName} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
          </div>
        </div>

        <div className="space-y-4 pt-2">
          <h3 className="font-serif font-semibold text-lg border-b border-border pb-2">Dates & Places</h3>
          <div className="grid sm:grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-text-primary">Date of Birth</label>
              <input type="text" name="dateOfBirth" placeholder="DD-MM-YYYY or YYYY" value={formData.dateOfBirth} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-text-primary">Current Residence (City, Country)</label>
              <input type="text" name="location" placeholder="E.g., Lahore, Pakistan" value={formData.location} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-text-primary">Date of Death (if deceased)</label>
              <input type="text" name="dateOfDeath" placeholder="DD-MM-YYYY or YYYY" value={formData.dateOfDeath} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
            <div className="space-y-1.5">
              <label className="text-sm font-medium text-text-primary">Burial Location</label>
              <input type="text" name="burialLocation" placeholder="Cemetery, City" value={formData.burialLocation} onChange={handleChange} className="w-full px-4 py-2.5 rounded-lg border border-border" />
            </div>
          </div>
        </div>

        <div className="space-y-4 pt-2">
          <h3 className="font-serif font-semibold text-lg border-b border-border pb-2">Biography</h3>
          <div className="space-y-1.5">
            <label className="text-sm font-medium text-text-primary">Life Story or Notes</label>
            <textarea name="biography" value={formData.biography} onChange={handleChange} rows={4} className="w-full px-4 py-2.5 rounded-lg border border-border resize-y bg-bg-primary focus:border-accent" placeholder="Share any memories, profession, or interesting facts..." />
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

"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchTree, type Member } from "@/lib/api";
import { User, Heart, Loader2, Plus, ZoomIn, ZoomOut, Maximize } from "lucide-react";
import { TransformWrapper, TransformComponent } from "react-zoom-pan-pinch";

function TreeCard({ member }: { member: Member }) {
  const genderBg =
    member.Gender === "Male"
      ? "bg-sky-light/80 border-sky/30 text-sky-900"
      : member.Gender === "Female"
      ? "bg-plum-light/80 border-plum/30 text-plum-900"
      : "bg-bg-secondary border-border text-text-primary";

  return (
    <Link href={`/member/${member.id}`} className="group inline-block">
      <div
        className={`flex flex-col items-center p-3 rounded-2xl border ${genderBg} shadow-sm hover:shadow-md transition-all min-w-[140px] relative`}
      >
        <div 
          className="w-12 h-12 rounded-full bg-white/70 flex items-center justify-center text-lg font-serif font-bold group-hover:-translate-y-1 transition-transform mb-2 bg-cover bg-center relative z-10"
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
    </Link>
  );
}

function FamilyTreeNode({ member }: { member: Member }) {
  return (
    <li>
      <TreeCard member={member} />
      {member.children && member.children.length > 0 && (
        <ul>
          {member.children.map((child) => (
            <FamilyTreeNode key={child.id} member={child} />
          ))}
        </ul>
      )}
    </li>
  );
}

export default function TreePage() {
  const [tree, setTree] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    fetchTree()
      .then(setTree)
      .catch((e) => setError(e.message))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="mx-auto max-w-7xl px-5 sm:px-8 py-12 sm:py-16 overflow-x-auto rendering-wrapper relative">
      <div className="mb-12 animate-fadeInUp flex items-end justify-between">
        <div>
          <p className="text-accent text-sm font-medium uppercase tracking-wide mb-2 flex items-center gap-2">
            <span className="w-6 h-px bg-accent" />
            Genealogy
          </p>
          <h1 className="heading-serif text-3xl sm:text-4xl font-bold mb-3">
            Family Tree
          </h1>
          <p className="text-text-muted text-base max-w-lg">
            Explore generations of heritage. Scroll horizontally to view wide branches of the family.
          </p>
        </div>
        {tree.length > 0 && (
          <Link href="/submit" className="btn-primary py-2 px-4 text-xs whitespace-nowrap hidden sm:flex">
            <Plus className="w-3.5 h-3.5" />
            Add Family Member
          </Link>
        )}
      </div>

      <style dangerouslySetInnerHTML={{
        __html: `
        .family-tree { display: flex; justify-content: center; padding-bottom: 3rem; }
        .family-tree ul { padding-top: 30px; position: relative; transition: all 0.5s; display: flex; justify-content: center; }
        .family-tree li { float: left; text-align: center; list-style-type: none; position: relative; padding: 30px 10px 0 10px; transition: all 0.5s; }
        .family-tree li::before, .family-tree li::after { content: ''; position: absolute; top: 0; right: 50%; border-top: 2px solid #dcd7cf; width: 50%; height: 30px; }
        .family-tree li::after { right: auto; left: 50%; border-left: 2px solid #dcd7cf; }
        .family-tree li:only-child::after, .family-tree li:only-child::before { display: none; }
        .family-tree li:only-child { padding-top: 0; }
        .family-tree li:first-child::before, .family-tree li:last-child::after { border: 0 none; }
        .family-tree li:last-child::before { border-right: 2px solid #dcd7cf; border-radius: 0 5px 0 0; }
        .family-tree li:first-child::after { border-radius: 5px 0 0 0; }
        .family-tree ul ul::before { content: ''; position: absolute; top: 0; left: 50%; border-left: 2px solid #dcd7cf; width: 0; height: 30px; transform: translateX(-1px); }
      `}} />

      {loading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="w-6 h-6 animate-spin text-accent" />
        </div>
      )}

      {!loading && !error && tree.length === 0 && (
        <div className="heritage-card p-14 text-center animate-fadeInUp">
          <div className="w-16 h-16 mx-auto mb-5 rounded-2xl bg-bg-secondary flex items-center justify-center">
            <User className="w-7 h-7 text-text-light" />
          </div>
          <h2 className="font-serif text-xl font-semibold mb-2">No family members yet</h2>
          <p className="text-text-muted text-sm mb-7 max-w-sm mx-auto">
            Start building your family tree by adding the very first member of your heritage.
          </p>
          <Link href="/submit" className="btn-primary">
            <Plus className="w-4 h-4" />
            Add First Member
          </Link>
        </div>
      )}

      {!loading && tree.length > 0 && (
        <div className="relative animate-fadeInUp bg-bg-secondary/30 rounded-3xl border border-border overflow-hidden" style={{ height: "65vh" }}>
          <TransformWrapper
            initialScale={1}
            minScale={0.2}
            maxScale={4}
            centerOnInit={true}
            wheel={{ step: 0.1 }}
          >
            {({ zoomIn, zoomOut, resetTransform }) => (
              <>
                <div className="absolute top-4 right-4 z-10 flex flex-col gap-1.5 bg-white/90 backdrop-blur-sm p-1.5 rounded-xl shadow-sm border border-border">
                  <button onClick={() => zoomIn()} className="p-2 hover:bg-bg-secondary rounded-lg text-text-muted hover:text-text-primary transition-colors" title="Zoom In">
                    <ZoomIn className="w-5 h-5" />
                  </button>
                  <button onClick={() => zoomOut()} className="p-2 hover:bg-bg-secondary rounded-lg text-text-muted hover:text-text-primary transition-colors" title="Zoom Out">
                    <ZoomOut className="w-5 h-5" />
                  </button>
                  <div className="h-px bg-border/50 w-full my-0.5"></div>
                  <button onClick={() => resetTransform()} className="p-2 hover:bg-bg-secondary rounded-lg text-text-muted hover:text-text-primary transition-colors" title="Reset View">
                    <Maximize className="w-4 h-4" />
                  </button>
                </div>
                
                <TransformComponent wrapperStyle={{ width: "100%", height: "100%", cursor: "grab" }}>
                  <div className="family-tree pt-16 pb-24 px-12 min-w-max">
                    <ul>
                      {tree.map((root) => (
                        <FamilyTreeNode
                          key={root.id}
                          member={root}
                        />
                      ))}
                    </ul>
                  </div>
                </TransformComponent>
              </>
            )}
          </TransformWrapper>
        </div>
      )}
    </div>
  );
}

"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { fetchMembers, type Member } from "@/lib/api";
import {
  TreePine,
  Users,
  MapPin,
  Search,
  ArrowRight,
  Heart,
  Clock,
  Globe,
} from "lucide-react";

export default function HomePage() {
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetchMembers()
      .then(setMembers)
      .catch(() => setMembers([]))
      .finally(() => setLoading(false));
  }, []);

  const stats = {
    total: members.length,
    alive: members.filter((m) => m.IsAlive).length,
    cities: new Set(members.map((m) => m.CurrentCity).filter(Boolean)).size,
    generations: Math.max(0, ...members.map((m) => m.Generation || 0)),
  };

  return (
    <div className="min-h-screen">
      {/* Hero */}
      <section className="relative overflow-hidden bg-bg-secondary">
        <div className="absolute inset-0 texture-grain" />
        <div className="relative mx-auto max-w-6xl px-5 sm:px-8 pt-16 sm:pt-24 pb-20 sm:pb-28">
          <div className="max-w-2xl animate-fadeInUp">
            <p className="inline-flex items-center gap-2 text-accent text-sm font-medium mb-5 tracking-wide uppercase">
              <span className="w-8 h-px bg-accent" />
              Family Heritage
            </p>

            <h1 className="heading-serif text-4xl sm:text-5xl lg:text-6xl font-bold leading-[1.1] mb-6">
              Welcome to our{" "}
              <span className="italic text-accent-warm">family</span> archive.
            </h1>

            <p className="text-text-secondary text-lg leading-relaxed mb-10 max-w-xl">
              This is our private space to explore generations of our shared heritage,
              discover where our ancestors have lived, and contribute to a living
              book of our memories.
            </p>

            <div className="flex flex-col sm:flex-row gap-3">
              <Link href="/tree" className="btn-primary text-[15px] py-3 px-7">
                <TreePine className="w-4 h-4" />
                Explore the Tree
                <ArrowRight className="w-4 h-4" />
              </Link>
              <Link
                href="/submit"
                className="btn-secondary text-[15px] py-3 px-7"
              >
                Submit Family Info
              </Link>
            </div>
          </div>
        </div>

        {/* Decorative element */}
        <div className="absolute right-0 top-1/2 -translate-y-1/2 w-[400px] h-[400px] opacity-[0.04] hidden lg:block">
          <TreePine className="w-full h-full text-accent" strokeWidth={0.5} />
        </div>
      </section>

      {/* Stats */}
      <section className="mx-auto max-w-6xl px-5 sm:px-8 -mt-10 relative z-10">
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 stagger-children">
          {[
            { label: "Family Members", value: loading ? "—" : stats.total, icon: Users, bg: "bg-sky-light", iconColor: "text-sky" },
            { label: "Living Members", value: loading ? "—" : stats.alive, icon: Heart, bg: "bg-emerald-light", iconColor: "text-emerald" },
            { label: "Cities", value: loading ? "—" : stats.cities, icon: Globe, bg: "bg-terracotta-light", iconColor: "text-terracotta" },
            { label: "Generations", value: loading ? "—" : stats.generations, icon: Clock, bg: "bg-plum-light", iconColor: "text-plum" },
          ].map((stat) => (
            <div key={stat.label} className="heritage-card p-5 flex items-center gap-4">
              <div className={`w-11 h-11 rounded-xl ${stat.bg} flex items-center justify-center flex-shrink-0`}>
                <stat.icon className={`w-5 h-5 ${stat.iconColor}`} />
              </div>
              <div>
                <div className="text-2xl font-bold text-text-primary font-serif">
                  {stat.value}
                </div>
                <div className="text-xs text-text-muted font-medium">{stat.label}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* Features */}
      <section className="mx-auto max-w-6xl px-5 sm:px-8 py-20 sm:py-28">
        <div className="text-center mb-14">
          <p className="divider-ornament text-sm font-medium uppercase tracking-widest text-text-light mb-4">
            Explore
          </p>
          <h2 className="heading-serif text-3xl sm:text-4xl font-bold">
            Discover Your Roots
          </h2>
        </div>

        <div className="grid md:grid-cols-3 gap-6 stagger-children">
          {[
            {
              title: "Interactive Tree",
              description:
                "Navigate through generations with an expandable family tree. Click any member to uncover their full story.",
              icon: TreePine,
              href: "/tree",
              accent: "bg-emerald-light",
              iconColor: "text-emerald",
            },
            {
              title: "Heritage Map",
              description:
                "See where family members live and rest on a world map. Every pin connects to a life story.",
              icon: MapPin,
              href: "/map",
              accent: "bg-terracotta-light",
              iconColor: "text-terracotta",
            },
            {
              title: "Search & Filter",
              description:
                "Find any family member by name, location, generation, or family branch in seconds.",
              icon: Search,
              href: "/search",
              accent: "bg-sky-light",
              iconColor: "text-sky",
            },
          ].map((card) => (
            <Link key={card.title} href={card.href} className="group">
              <div className="heritage-card p-7 h-full">
                <div
                  className={`w-12 h-12 rounded-xl ${card.accent} flex items-center justify-center mb-5 group-hover:scale-105 transition-heritage`}
                >
                  <card.icon className={`w-5 h-5 ${card.iconColor}`} />
                </div>
                <h3 className="font-serif text-xl font-semibold mb-2.5 text-text-primary">
                  {card.title}
                </h3>
                <p className="text-text-muted text-sm leading-relaxed mb-5">
                  {card.description}
                </p>
                <span className="inline-flex items-center gap-1.5 text-accent text-sm font-medium group-hover:gap-2.5 transition-all">
                  Explore
                  <ArrowRight className="w-3.5 h-3.5" />
                </span>
              </div>
            </Link>
          ))}
        </div>
      </section>

      {/* Members */}
      {members.length > 0 && (
        <section className="bg-bg-secondary">
          <div className="mx-auto max-w-6xl px-5 sm:px-8 py-20">
            <div className="text-center mb-12">
              <p className="divider-ornament text-sm font-medium uppercase tracking-widest text-text-light mb-4">
                Family
              </p>
              <h2 className="heading-serif text-3xl sm:text-4xl font-bold">
                Our Family Members
              </h2>
            </div>

            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-4 stagger-children">
              {members.slice(0, 10).map((member) => (
                <Link key={member.id} href={`/member/${member.id}`} className="group">
                  <div className="heritage-card p-5 text-center">
                    <div className="w-14 h-14 mx-auto mb-3 rounded-full bg-bg-secondary flex items-center justify-center text-lg font-serif font-bold text-accent group-hover:bg-accent group-hover:text-white transition-heritage">
                      {(member.FullName || "?")[0]}
                    </div>
                    <div className="font-medium text-sm text-text-primary truncate">
                      {member.FullName || "Unknown"}
                    </div>
                    <div className="text-xs text-text-muted mt-1 truncate">
                      {member.CurrentCity || ""}
                      {member.CurrentCity && member.CurrentCountry ? ", " : ""}
                      {member.CurrentCountry || ""}
                    </div>
                  </div>
                </Link>
              ))}
            </div>

            {members.length > 10 && (
              <div className="text-center mt-10">
                <Link href="/search" className="btn-secondary">
                  View all {members.length} members
                  <ArrowRight className="w-4 h-4" />
                </Link>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Footer */}
      <footer className="border-t border-border py-10">
        <div className="mx-auto max-w-6xl px-5 sm:px-8 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2">
            <TreePine className="w-4 h-4 text-accent" />
            <span className="font-serif text-sm font-semibold text-text-primary">Shajra</span>
          </div>
          <p className="text-xs text-text-light">
            Preserving family heritage for generations to come.
          </p>
        </div>
      </footer>
    </div>
  );
}

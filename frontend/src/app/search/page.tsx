"use client";

import { useEffect, useState, useMemo } from "react";
import { fetchMembers, type Member } from "@/lib/api";
import Link from "next/link";
import {
  Search as SearchIcon,
  User,
  MapPin,
  Filter,
  X,
  Loader2,
  Heart,
} from "lucide-react";

export default function SearchPage() {
  const [members, setMembers] = useState<Member[]>([]);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState("");
  const [filterCity, setFilterCity] = useState("");
  const [filterBranch, setFilterBranch] = useState("");
  const [filterGeneration, setFilterGeneration] = useState("");

  useEffect(() => {
    fetchMembers()
      .then(setMembers)
      .catch(() => setMembers([]))
      .finally(() => setLoading(false));
  }, []);

  const cities = useMemo(
    () => [...new Set(members.map((m) => m.CurrentCity).filter(Boolean))].sort(),
    [members]
  );
  const branches = useMemo(
    () => [...new Set(members.map((m) => m.Branch).filter(Boolean))].sort(),
    [members]
  );
  const generations = useMemo(
    () =>
      [...new Set(members.map((m) => m.Generation).filter((g) => g !== undefined && g !== null))]
        .sort((a, b) => (a as number) - (b as number)),
    [members]
  );

  const filtered = useMemo(() => {
    return members.filter((m) => {
      const matchesQuery =
        !query ||
        (m.FullName || "").toLowerCase().includes(query.toLowerCase()) ||
        (m.FatherName || "").toLowerCase().includes(query.toLowerCase());
      const matchesCity = !filterCity || m.CurrentCity === filterCity;
      const matchesBranch = !filterBranch || m.Branch === filterBranch;
      const matchesGen = !filterGeneration || String(m.Generation) === filterGeneration;
      return matchesQuery && matchesCity && matchesBranch && matchesGen;
    });
  }, [members, query, filterCity, filterBranch, filterGeneration]);

  const hasFilters = filterCity || filterBranch || filterGeneration;

  return (
    <div className="mx-auto max-w-4xl px-5 sm:px-8 py-12 sm:py-16">
      <div className="mb-10 animate-fadeInUp">
        <p className="text-accent text-sm font-medium uppercase tracking-wide mb-2 flex items-center gap-2">
          <span className="w-6 h-px bg-accent" />
          Directory
        </p>
        <h1 className="heading-serif text-3xl sm:text-4xl font-bold mb-3">
          Search & Discover
        </h1>
        <p className="text-text-muted text-base">
          Find any family member by name, location, or generation.
        </p>
      </div>

      {/* Search */}
      <div className="heritage-card p-4 mb-5">
        <div className="relative">
          <SearchIcon className="absolute left-4 top-1/2 -translate-y-1/2 w-4 h-4 text-text-light" />
          <input
            type="text"
            placeholder="Search by name..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            className="input-heritage"
            style={{ paddingLeft: "2.75rem" }}
          />
        </div>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2.5 mb-8">
        <Filter className="w-3.5 h-3.5 text-text-light" />
        <select
          value={filterCity}
          onChange={(e) => setFilterCity(e.target.value)}
          className="input-heritage w-auto text-[13px] py-2"
        >
          <option value="">All Cities</option>
          {cities.map((city) => (
            <option key={city} value={city}>{city}</option>
          ))}
        </select>

        {branches.length > 0 && (
          <select
            value={filterBranch}
            onChange={(e) => setFilterBranch(e.target.value)}
            className="input-heritage w-auto text-[13px] py-2"
          >
            <option value="">All Branches</option>
            {branches.map((b) => (
              <option key={b} value={b}>{b}</option>
            ))}
          </select>
        )}

        {generations.length > 0 && (
          <select
            value={filterGeneration}
            onChange={(e) => setFilterGeneration(e.target.value)}
            className="input-heritage w-auto text-[13px] py-2"
          >
            <option value="">All Generations</option>
            {generations.map((gen) => (
              <option key={gen} value={String(gen)}>Gen {gen}</option>
            ))}
          </select>
        )}

        {hasFilters && (
          <button
            onClick={() => {
              setFilterCity("");
              setFilterBranch("");
              setFilterGeneration("");
            }}
            className="flex items-center gap-1 px-2.5 py-1.5 text-xs text-terracotta hover:bg-terracotta-light rounded-lg transition-heritage"
          >
            <X className="w-3 h-3" />
            Clear
          </button>
        )}

        <span className="ml-auto text-xs text-text-light">
          {filtered.length} result{filtered.length !== 1 ? "s" : ""}
        </span>
      </div>

      {loading && (
        <div className="flex items-center justify-center py-20">
          <Loader2 className="w-6 h-6 animate-spin text-accent" />
        </div>
      )}

      {!loading && filtered.length === 0 && (
        <div className="heritage-card p-14 text-center">
          <User className="w-10 h-10 mx-auto mb-4 text-text-light" />
          <h2 className="font-serif text-xl font-semibold mb-2">No results found</h2>
          <p className="text-text-muted text-sm">Try a different search or filter.</p>
        </div>
      )}

      {!loading && filtered.length > 0 && (
        <div className="space-y-2.5 stagger-children">
          {filtered.map((member) => (
            <Link key={member.id} href={`/member/${member.id}`} className="group block">
              <div className="heritage-card p-4 flex items-center gap-4">
                <div className="w-11 h-11 rounded-xl bg-accent/8 flex items-center justify-center text-base font-serif font-bold text-accent flex-shrink-0 group-hover:bg-accent group-hover:text-white transition-heritage">
                  {(member.FullName || "?")[0]}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="font-semibold text-sm text-text-primary truncate flex items-center gap-2">
                    {member.FullName || "Unknown"}
                    {member.IsAlive && (
                      <Heart className="w-3 h-3 text-emerald fill-emerald" />
                    )}
                  </div>
                  <div className="flex items-center gap-3 text-xs text-text-muted mt-0.5">
                    {member.FatherName && <span>s/o {member.FatherName}</span>}
                    {member.CurrentCity && (
                      <span className="flex items-center gap-1">
                        <MapPin className="w-3 h-3" />
                        {member.CurrentCity}
                      </span>
                    )}
                    {member.Generation && (
                      <span className="px-1.5 py-0.5 rounded bg-bg-secondary text-text-muted text-[11px]">
                        Gen {member.Generation}
                      </span>
                    )}
                    {member.Branch && (
                      <span className="px-1.5 py-0.5 rounded bg-accent/6 text-accent text-[11px]">
                        {member.Branch}
                      </span>
                    )}
                  </div>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

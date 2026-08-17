"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { searchMembers, type Member, type SearchFilters } from "@/lib/api";
import TiltCard from "@/components/ui/TiltCard";
import AsyncState from "@/components/feedback/AsyncState";
import { asApiProblem, type Loadable } from "@/lib/loadable";
import Link from "next/link";
import {
  Search as SearchIcon,
  User,
  MapPin,
  Filter,
  X,
  Heart,
  Mail,
  MessageCircle,
} from "lucide-react";

const DEBOUNCE_MS = 300;

/** Normalize a phone number into a wa.me-friendly digits string. */
function whatsappHref(phone: string | undefined): string {
  if (!phone) return "";
  let digits = phone.replace(/[^\d]/g, "");
  if (digits.startsWith("0")) digits = "92" + digits.slice(1);
  return `https://wa.me/${digits}`;
}

export default function SearchPage() {
  // Full member list used only to populate the filter dropdown options.
  const [facets, setFacets] = useState<Member[]>([]);
  const [resultState, setResultState] = useState<Loadable<Member[]>>({ status: "loading" });
  const resultRequest = useRef(0);

  const [query, setQuery] = useState("");
  const [filterCity, setFilterCity] = useState("");
  const [filterBranch, setFilterBranch] = useState("");
  const [filterGeneration, setFilterGeneration] = useState("");
  const [retryNonce, setRetryNonce] = useState(0);

  const filters = useMemo<SearchFilters>(
    () => ({ city: filterCity, branch: filterBranch, generation: filterGeneration }),
    [filterCity, filterBranch, filterGeneration],
  );

  // Debounce the free-text query so we only hit /api/search once the user pauses.
  const [debouncedQuery, setDebouncedQuery] = useState("");
  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(query), DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [query]);

  // Run the search whenever the debounced query or a filter changes. The very
  // first run (empty query, no filters) also seeds the filter dropdown options.
  useEffect(() => {
    const request = ++resultRequest.current;
    setResultState({ status: "loading" });
    searchMembers(debouncedQuery, filters).then(
      (data) => {
        if (request !== resultRequest.current) return;
        if (!debouncedQuery && !filters.city && !filters.branch && !filters.generation) {
          setFacets(data);
        }
        setResultState(data.length > 0 ? { status: "ready", data } : { status: "empty", data });
      },
      (error: unknown) => {
        if (request !== resultRequest.current) return;
        setResultState({
          status: "error",
          problem: asApiProblem(error, "The family directory could not be loaded."),
        });
      },
    );
    return () => {
      resultRequest.current += 1;
    };
  }, [debouncedQuery, filters, retryNonce]);

  const retryMembers = () => {
    setResultState({ status: "loading" });
    setRetryNonce((n) => n + 1);
  };

  const members = useMemo(
    () => ("data" in resultState ? resultState.data : []),
    [resultState],
  );

  const cities = useMemo(
    () => [...new Set(facets.map((m) => m.CurrentCity).filter(Boolean))].sort(),
    [facets],
  );
  const branches = useMemo(
    () => [...new Set(facets.map((m) => m.Branch).filter(Boolean))].sort(),
    [facets],
  );
  const generations = useMemo(
    () =>
      [...new Set(facets.map((m) => m.Generation).filter((g) => g !== undefined && g !== null))]
        .sort((a, b) => (a as number) - (b as number)),
    [facets],
  );

  const hasFilters = Boolean(filterCity || filterBranch || filterGeneration);
  const hasActiveQuery = query.trim().length >= 2;

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
            aria-label="Search by name"
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
          aria-label="City"
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
            aria-label="Branch"
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
            aria-label="Generation"
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
          {resultState.status === "ready" || resultState.status === "empty"
            ? `${members.length} result${members.length !== 1 ? "s" : ""}`
            : "Results unavailable"}
        </span>
      </div>

      {resultState.status === "loading" && (
        <AsyncState state="loading" title="Loading family directory" />
      )}

      {resultState.status === "error" && (
        <AsyncState
          state="error"
          title="Directory unavailable"
          message={resultState.problem.message}
          actionLabel="Retry"
          onAction={retryMembers}
        />
      )}

      {resultState.status === "empty" && !hasActiveQuery && !hasFilters && (
        <div className="heritage-card p-14 text-center">
          <User className="w-10 h-10 mx-auto mb-4 text-text-light" />
          <h2 className="font-serif text-xl font-semibold mb-2">No family members yet</h2>
          <p className="text-text-muted text-sm">The family directory has no records yet.</p>
        </div>
      )}

      {resultState.status === "empty" && (hasActiveQuery || hasFilters) && (
        <div className="heritage-card p-14 text-center">
          <User className="w-10 h-10 mx-auto mb-4 text-text-light" />
          <h2 className="font-serif text-xl font-semibold mb-2">No matching members</h2>
          <p className="text-text-muted text-sm">Try a different search or filter.</p>
        </div>
      )}

      {resultState.status === "ready" && members.length > 0 && (
        <div className="space-y-2.5 stagger-children">
          {members.map((member) => (
            <div key={member.id} className="group">
              <TiltCard maxTilt={5} className="rounded-xl">
              <div className="heritage-card p-4 flex items-center gap-4">
                <Link href={`/member/${member.id}`} className="flex items-center gap-4 flex-1 min-w-0">
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
                </Link>
                <div className="flex items-center gap-1.5 flex-shrink-0">
                  {member.Email && (
                    <a
                      href={`mailto:${member.Email}`}
                      title={`Email ${member.Email}`}
                      className="p-2 rounded-lg bg-accent/8 text-accent hover:bg-accent hover:text-white transition-colors"
                    >
                      <Mail className="w-3.5 h-3.5" />
                    </a>
                  )}
                  {member.PhoneNumber && (
                    <a
                      href={whatsappHref(member.PhoneNumber)}
                      target="_blank"
                      rel="noopener noreferrer"
                      title={`WhatsApp ${member.PhoneNumber}`}
                      className="p-2 rounded-lg bg-emerald/10 text-emerald hover:bg-emerald hover:text-white transition-colors"
                    >
                      <MessageCircle className="w-3.5 h-3.5" />
                    </a>
                  )}
                </div>
              </div>
              </TiltCard>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

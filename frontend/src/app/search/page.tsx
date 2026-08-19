"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { Filter, Heart, MapPin, Search as SearchIcon, User, X } from "lucide-react";

import AsyncState from "@/components/feedback/AsyncState";
import TiltCard from "@/components/ui/TiltCard";
import { searchMembers, type Member, type SearchFilters } from "@/lib/api";
import { asApiProblem } from "@/lib/loadable";
import type { ApiProblem } from "@/lib/http";

const DEBOUNCE_MS = 300;

type SearchState =
  | { status: "idle" }
  | { status: "ready"; data: Member[]; requestKey: string }
  | { status: "empty"; data: Member[]; requestKey: string }
  | { status: "error"; problem: ApiProblem; requestKey: string };

export default function SearchPage() {
  const [resultState, setResultState] = useState<SearchState>({ status: "idle" });
  const resultRequest = useRef(0);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [filterCity, setFilterCity] = useState("");
  const [filterBranch, setFilterBranch] = useState("");
  const [filterGeneration, setFilterGeneration] = useState("");
  const [retryNonce, setRetryNonce] = useState(0);

  const filters = useMemo<SearchFilters>(() => {
    const next: SearchFilters = {};
    const city = filterCity.trim();
    const branch = filterBranch.trim();
    const generation = filterGeneration.trim();
    if (city) next.city = city;
    if (branch) next.branch = branch;
    if (generation) next.generation = generation;
    return next;
  }, [filterBranch, filterCity, filterGeneration]);

  const hasFilters = Object.keys(filters).length > 0;
  const trimmedQuery = query.trim();
  const trimmedDebouncedQuery = debouncedQuery.trim();
  const hasActiveQuery = trimmedQuery.length >= 2;
  const shouldShowSearch = hasActiveQuery || hasFilters;
  const canRequest = trimmedDebouncedQuery.length >= 2 || hasFilters;
  const requestKey = useMemo(
    () => JSON.stringify([trimmedDebouncedQuery, filters, retryNonce]),
    [filters, retryNonce, trimmedDebouncedQuery],
  );

  useEffect(() => {
    const handle = setTimeout(() => setDebouncedQuery(query), DEBOUNCE_MS);
    return () => clearTimeout(handle);
  }, [query]);

  useEffect(() => {
    if (!canRequest) {
      resultRequest.current += 1;
      return;
    }

    const request = ++resultRequest.current;
    searchMembers(trimmedDebouncedQuery, filters).then(
      (data) => {
        if (request !== resultRequest.current) return;
        setResultState(
          data.length > 0
            ? { status: "ready", data, requestKey }
            : { status: "empty", data, requestKey },
        );
      },
      (error: unknown) => {
        if (request !== resultRequest.current) return;
        setResultState({
          status: "error",
          problem: asApiProblem(error, "The family directory could not be loaded."),
          requestKey,
        });
      },
    );

    return () => {
      if (request === resultRequest.current) resultRequest.current += 1;
    };
  }, [canRequest, filters, requestKey, trimmedDebouncedQuery]);

  const isDebouncing = trimmedQuery !== trimmedDebouncedQuery;
  const isLoading =
    shouldShowSearch &&
    (isDebouncing || resultState.status === "idle" || resultState.requestKey !== requestKey);
  const visibleState: SearchState | { status: "loading" } = !shouldShowSearch
    ? { status: "idle" }
    : isLoading
      ? { status: "loading" }
      : resultState;
  const members = "data" in visibleState ? visibleState.data : [];

  const clearFilters = () => {
    setFilterCity("");
    setFilterBranch("");
    setFilterGeneration("");
  };

  return (
    <div className="mx-auto max-w-4xl px-5 py-12 sm:px-8 sm:py-16">
      <div className="mb-10 animate-fadeInUp">
        <p className="mb-2 flex items-center gap-2 text-sm font-medium uppercase tracking-wide text-accent">
          <span className="h-px w-6 bg-accent" />
          Directory
        </p>
        <h1 className="heading-serif mb-3 text-3xl font-bold sm:text-4xl">
          Search &amp; Discover
        </h1>
        <p className="text-base text-text-muted">
          Find family members by name, location, branch, or generation.
        </p>
      </div>

      <div className="heritage-card mb-5 p-4">
        <div className="relative">
          <SearchIcon className="absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-text-light" />
          <input
            type="search"
            aria-label="Search by name"
            placeholder="Search by name..."
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="input-heritage"
            style={{ paddingLeft: "2.75rem" }}
          />
        </div>
      </div>

      <div className="mb-8 grid min-w-0 grid-cols-1 items-center gap-2.5 sm:grid-cols-3 lg:grid-cols-[auto_repeat(3,minmax(0,1fr))_auto_auto]">
        <Filter className="hidden h-3.5 w-3.5 text-text-light lg:block" />
        <input
          type="text"
          aria-label="City"
          placeholder="City"
          value={filterCity}
          onChange={(event) => setFilterCity(event.target.value)}
          className="input-heritage min-w-0 py-2 text-[13px]"
        />
        <input
          type="text"
          aria-label="Branch"
          placeholder="Branch"
          value={filterBranch}
          onChange={(event) => setFilterBranch(event.target.value)}
          className="input-heritage min-w-0 py-2 text-[13px]"
        />
        <input
          type="number"
          min="1"
          inputMode="numeric"
          aria-label="Generation"
          placeholder="Generation"
          value={filterGeneration}
          onChange={(event) => setFilterGeneration(event.target.value)}
          className="input-heritage min-w-0 py-2 text-[13px]"
        />

        {hasFilters && (
          <button
            type="button"
            onClick={clearFilters}
            className="flex items-center justify-center gap-1 rounded-lg px-2.5 py-2 text-xs text-terracotta transition-heritage hover:bg-terracotta-light"
          >
            <X className="h-3 w-3" />
            Clear
          </button>
        )}

        <span className="text-xs text-text-light sm:col-span-3 lg:col-span-1 lg:ml-auto">
          {visibleState.status === "ready" || visibleState.status === "empty"
            ? `${members.length} result${members.length === 1 ? "" : "s"}`
            : visibleState.status === "loading"
              ? "Searching..."
              : visibleState.status === "error"
                ? "Results unavailable"
                : "No search yet"}
        </span>
      </div>

      {visibleState.status === "idle" && (
        <div className="heritage-card p-10 text-center sm:p-14">
          <SearchIcon className="mx-auto mb-4 h-10 w-10 text-text-light" />
          <h2 className="mb-2 font-serif text-xl font-semibold">Search by name or filter</h2>
          <p className="text-sm text-text-muted">
            Enter at least 2 characters or add a filter.
          </p>
        </div>
      )}

      {visibleState.status === "loading" && (
        <AsyncState state="loading" title="Searching family directory" />
      )}

      {visibleState.status === "error" && (
        <AsyncState
          state="error"
          title="Directory unavailable"
          message={visibleState.problem.message}
          actionLabel="Retry"
          onAction={() => setRetryNonce((nonce) => nonce + 1)}
        />
      )}

      {visibleState.status === "empty" && (
        <div className="heritage-card p-10 text-center sm:p-14">
          <User className="mx-auto mb-4 h-10 w-10 text-text-light" />
          <h2 className="mb-2 font-serif text-xl font-semibold">No matching members</h2>
          <p className="text-sm text-text-muted">Try a different search or filter.</p>
        </div>
      )}

      {visibleState.status === "ready" && members.length > 0 && (
        <div className="stagger-children space-y-2.5">
          {members.map((member) => (
            <div key={member.id} className="group min-w-0">
              <TiltCard maxTilt={5} className="min-w-0 rounded-lg">
                <div className="heritage-card min-w-0 p-4">
                  <Link
                    href={`/member/${member.id}`}
                    className="grid min-w-0 grid-cols-[2.75rem_minmax(0,1fr)] items-start gap-3 sm:gap-4"
                  >
                    <div className="flex h-11 w-11 flex-shrink-0 items-center justify-center rounded-lg bg-accent/8 font-serif text-base font-bold text-accent transition-heritage group-hover:bg-accent group-hover:text-white">
                      {(member.FullName || "?")[0]}
                    </div>
                    <div className="min-w-0">
                      <div className="flex min-w-0 flex-wrap items-center gap-2 text-sm font-semibold text-text-primary">
                        <span className="min-w-0 break-words">
                          {member.FullName || "Unknown"}
                        </span>
                        {member.IsAlive && (
                          <Heart className="h-3 w-3 flex-shrink-0 fill-emerald text-emerald" />
                        )}
                      </div>
                      <div className="mt-1 flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-muted">
                        {member.FatherName && (
                          <span className="min-w-0 break-words">s/o {member.FatherName}</span>
                        )}
                        {member.CurrentCity && (
                          <span className="flex min-w-0 max-w-full items-start gap-1 break-words">
                            <MapPin className="mt-0.5 h-3 w-3 flex-shrink-0" />
                            {member.CurrentCity}
                          </span>
                        )}
                        {member.Generation && (
                          <span className="rounded bg-bg-secondary px-1.5 py-0.5 text-[11px] text-text-muted">
                            Gen {member.Generation}
                          </span>
                        )}
                        {member.Branch && (
                          <span className="min-w-0 break-words rounded bg-accent/6 px-1.5 py-0.5 text-[11px] text-accent">
                            {member.Branch}
                          </span>
                        )}
                      </div>
                    </div>
                  </Link>
                </div>
              </TiltCard>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

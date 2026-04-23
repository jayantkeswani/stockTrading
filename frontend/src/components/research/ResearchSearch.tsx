"use client";

import { useState, useCallback, useRef, useEffect } from "react";
import { api } from "@/lib/api";

interface SearchResult {
  symbol: string;
  display: string;
  short_name: string;
  segment: string;
}

export function ResearchSearch({
  onStartResearch,
  isResearching,
}: {
  onStartResearch: (symbol: string) => void;
  isResearching: boolean;
}) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [showDropdown, setShowDropdown] = useState(false);
  const timerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const containerRef = useRef<HTMLDivElement>(null);

  const search = useCallback(async (q: string) => {
    if (q.length < 1) {
      setResults([]);
      return;
    }
    try {
      const data = await api.searchSymbols(q);
      const filtered = (data.results || []).filter(
        (r: SearchResult) => r.segment === "EQ" || r.segment === "INDEX"
      );
      setResults(filtered.slice(0, 8));
      setShowDropdown(filtered.length > 0);
    } catch {
      setResults([]);
    }
  }, []);

  const handleInput = (value: string) => {
    setQuery(value);
    clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => search(value), 300);
  };

  const handleSelect = (symbol: string) => {
    setQuery(symbol);
    setShowDropdown(false);
    setResults([]);
  };

  const handleSubmit = () => {
    const symbol = query.trim().toUpperCase();
    if (symbol) {
      onStartResearch(symbol);
      setShowDropdown(false);
    }
  };

  // Click outside to close
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        setShowDropdown(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, []);

  return (
    <div ref={containerRef} className="relative flex items-center gap-2">
      <div className="relative flex-1">
        <input
          type="text"
          value={query}
          onChange={(e) => handleInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
          placeholder="Search stock... (e.g., TCS, RELIANCE)"
          className="w-full bg-bg-tertiary border border-border rounded px-3 py-1.5 text-xs font-mono text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent"
        />

        {showDropdown && results.length > 0 && (
          <div className="absolute top-full left-0 right-0 mt-1 bg-bg-elevated border border-border rounded shadow-lg z-50 max-h-60 overflow-y-auto">
            {results.map((r) => (
              <button
                key={r.symbol}
                onClick={() => handleSelect(r.short_name)}
                className="w-full text-left px-3 py-1.5 text-xs font-mono hover:bg-bg-tertiary transition-colors flex items-center justify-between"
              >
                <span className="text-text-primary">{r.short_name}</span>
                <span className="text-text-muted truncate ml-2 max-w-[200px]">
                  {r.display}
                </span>
              </button>
            ))}
          </div>
        )}
      </div>

      <button
        onClick={handleSubmit}
        disabled={!query.trim() || isResearching}
        className="px-4 py-1.5 text-xs font-mono font-medium rounded bg-accent/15 text-accent hover:bg-accent/25 disabled:opacity-40 disabled:cursor-not-allowed transition-colors whitespace-nowrap"
      >
        {isResearching ? "Analyzing..." : "Analyze"}
      </button>
    </div>
  );
}

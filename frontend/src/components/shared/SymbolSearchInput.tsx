"use client";

import { useState, useEffect, useRef } from "react";
import { api } from "@/lib/api";

export interface SymbolResult {
  symbol: string;
  display: string;
  short_name: string;
  segment: string;
  strike: number;
  type: string;
  ltp: number;
  expiry: string;
  lot_size: number;
}

interface Props {
  onSelect: (result: SymbolResult) => void;
  placeholder?: string;
  segmentFilter?: string;
}

export function SymbolSearchInput({ onSelect, placeholder = "search symbols...", segmentFilter }: Props) {
  const [inputValue, setInputValue] = useState("");
  const [suggestions, setSuggestions] = useState<SymbolResult[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [searching, setSearching] = useState(false);
  const [filteredOut, setFilteredOut] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => {
    if (inputValue.trim().length < 1) {
      setSuggestions([]);
      setShowSuggestions(false);
      setFilteredOut(false);
      return;
    }

    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setSearching(true);
      try {
        const result = await api.searchSymbols(inputValue.trim());
        const filtered = segmentFilter
          ? result.results.filter((s: SymbolResult) => s.segment === segmentFilter)
          : result.results;
        const wasFilteredOut = segmentFilter !== undefined && result.results.length > 0 && filtered.length === 0;
        setFilteredOut(wasFilteredOut);
        setSuggestions(filtered);
        setShowSuggestions(filtered.length > 0 || wasFilteredOut);
      } catch {
        setSuggestions([]);
      }
      setSearching(false);
    }, 300);

    return () => clearTimeout(debounceRef.current);
  }, [inputValue, segmentFilter]);

  const handleSelect = (result: SymbolResult) => {
    onSelect(result);
    setInputValue("");
    setSuggestions([]);
    setShowSuggestions(false);
  };

  return (
    <div className="px-2 py-1.5 border-t border-border relative">
      <input
        type="text"
        value={inputValue}
        onChange={(e) => setInputValue(e.target.value)}
        onKeyDown={(e) => e.key === "Escape" && setShowSuggestions(false)}
        onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
        onBlur={() => setTimeout(() => setShowSuggestions(false), 200)}
        placeholder={placeholder}
        className="w-full text-xs font-mono bg-bg-tertiary border border-border rounded px-2 py-1 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/50 transition-colors"
      />
      {searching && (
        <div className="absolute right-4 top-2.5 text-[10px] text-text-muted font-mono">...</div>
      )}

      {showSuggestions && (filteredOut ? (
        <div className="absolute left-2 right-2 bottom-full mb-1 bg-bg-secondary border border-border rounded shadow-lg z-50 px-2 py-1.5">
          <span className="text-[10px] font-mono text-loss">Only equity stocks can be added here</span>
        </div>
      ) : suggestions.length > 0 && (
        <div className="absolute left-2 right-2 bottom-full mb-1 bg-bg-secondary border border-border rounded shadow-lg max-h-[200px] overflow-y-auto z-50">
          {suggestions.map((s) => (
            <button
              key={s.symbol}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => handleSelect(s)}
              className="w-full flex items-center justify-between px-2 py-1 hover:bg-bg-tertiary/80 transition-colors text-left border-b border-border/20 last:border-0"
            >
              <div className="min-w-0 flex-1">
                <span className="text-xs font-mono font-medium text-text-primary truncate">
                  {s.display}
                </span>
                {s.segment && (
                  <span className={`ml-1 text-[10px] font-mono ${
                    s.segment === "FUT" ? "text-warning" : s.segment === "OPT" ? "text-profit" : "text-text-muted"
                  }`}>
                    {s.segment}
                  </span>
                )}
                {s.expiry && (
                  <span className="ml-1 text-[10px] text-text-muted font-mono">
                    {s.expiry}
                  </span>
                )}
              </div>
              {s.type && (
                <span className={`text-[10px] font-mono font-medium ${
                  s.type === "CE" ? "text-profit" : "text-loss"
                }`}>
                  {s.type}
                </span>
              )}
            </button>
          ))}
        </div>
      ))}
    </div>
  );
}

"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { useStore } from "@/store";
import { SYMBOLS } from "@/lib/constants";
import { formatINR, pnlColor } from "@/lib/formatters";
import { api } from "@/lib/api";

interface WatchlistProps {
  onOpenChart: (symbol: string) => void;
}

interface SymbolResult {
  symbol: string;
  display: string;
  strike: number;
  type: string;
  ltp: number;
  expiry: string;
}

export function Watchlist({ onOpenChart }: WatchlistProps) {
  const { prices } = useStore();
  const [customContracts, setCustomContracts] = useState<
    { symbol: string; display: string }[]
  >([]);
  const [inputValue, setInputValue] = useState("");
  const [suggestions, setSuggestions] = useState<SymbolResult[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [searching, setSearching] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const inputRef = useRef<HTMLInputElement>(null);

  // Debounced symbol search
  useEffect(() => {
    if (inputValue.trim().length < 2) {
      setSuggestions([]);
      setShowSuggestions(false);
      return;
    }

    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      setSearching(true);
      try {
        const result = await api.searchSymbols(inputValue.trim());
        setSuggestions(result.results);
        setShowSuggestions(result.results.length > 0);
      } catch {
        setSuggestions([]);
      }
      setSearching(false);
    }, 300);

    return () => clearTimeout(debounceRef.current);
  }, [inputValue]);

  const handleSelectSuggestion = useCallback(
    (result: SymbolResult) => {
      if (!customContracts.some((c) => c.symbol === result.symbol)) {
        setCustomContracts((prev) => [
          ...prev,
          { symbol: result.symbol, display: result.display },
        ]);
      }
      setInputValue("");
      setSuggestions([]);
      setShowSuggestions(false);
    },
    [customContracts]
  );

  const handleRemoveContract = useCallback((symbol: string) => {
    setCustomContracts((prev) => prev.filter((c) => c.symbol !== symbol));
  }, []);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === "Escape") {
        setShowSuggestions(false);
      }
    },
    []
  );

  return (
    <div className="rounded-lg border border-border bg-bg-secondary">
      <div className="px-4 py-3 border-b border-border">
        <h2 className="text-sm font-semibold text-text-primary">Watchlist</h2>
      </div>

      <div className="divide-y divide-border/50">
        {/* Default indices */}
        {SYMBOLS.map((symbol) => {
          const price = prices[symbol];
          const changePct = price?.change_pct ?? 0;
          const change = price?.change ?? 0;

          return (
            <button
              key={symbol}
              onClick={() => onOpenChart(symbol)}
              className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-bg-tertiary/50 transition-colors text-left"
            >
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-text-primary">
                  {symbol}
                </span>
              </div>
              <div className="text-right">
                <div className="text-sm font-mono text-text-primary">
                  {price ? formatINR(price.ltp) : "--"}
                </div>
                {price && (
                  <div className={`text-xs font-mono ${pnlColor(changePct)}`}>
                    {changePct > 0 ? "\u25B2" : changePct < 0 ? "\u25BC" : ""}{" "}
                    {changePct > 0 ? "+" : ""}
                    {changePct.toFixed(2)}%
                    <span className="text-text-muted ml-1">
                      ({change != null && change > 0 ? "+" : ""}
                      {change?.toFixed(2) ?? "0"})
                    </span>
                  </div>
                )}
              </div>
            </button>
          );
        })}

        {/* Custom contracts */}
        {customContracts.map((contract) => {
          const price = prices[contract.symbol];
          const changePct = price?.change_pct ?? 0;

          return (
            <div
              key={contract.symbol}
              className="flex items-center justify-between px-4 py-2.5 hover:bg-bg-tertiary/50 transition-colors group"
            >
              <button
                onClick={() => onOpenChart(contract.symbol)}
                className="flex items-center gap-2 text-left flex-1"
              >
                <span className="text-sm font-medium text-text-primary">
                  {contract.display}
                </span>
              </button>
              <div className="flex items-center gap-2">
                <div className="text-right">
                  <div className="text-sm font-mono text-text-primary">
                    {price ? formatINR(price.ltp) : "--"}
                  </div>
                  {price && (
                    <div className={`text-xs font-mono ${pnlColor(changePct)}`}>
                      {changePct > 0 ? "+" : ""}
                      {changePct.toFixed(2)}%
                    </div>
                  )}
                </div>
                <button
                  onClick={() => handleRemoveContract(contract.symbol)}
                  className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-loss transition-all w-5 h-5 flex items-center justify-center rounded hover:bg-loss/20"
                >
                  <svg
                    className="w-3 h-3"
                    fill="none"
                    viewBox="0 0 24 24"
                    stroke="currentColor"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M6 18L18 6M6 6l12 12"
                    />
                  </svg>
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Add contract input with autocomplete */}
      <div className="px-3 py-2 border-t border-border relative">
        <input
          ref={inputRef}
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 200)}
          placeholder="Search: NIFTY 24000 CE..."
          className="w-full text-xs bg-bg-tertiary border border-border rounded px-2 py-1.5 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent transition-colors"
        />
        {searching && (
          <div className="absolute right-5 top-3.5 text-xs text-text-muted">
            ...
          </div>
        )}

        {/* Autocomplete dropdown */}
        {showSuggestions && suggestions.length > 0 && (
          <div className="absolute left-3 right-3 bottom-full mb-1 bg-bg-secondary border border-border rounded-lg shadow-lg max-h-[250px] overflow-y-auto z-50">
            {suggestions.map((s) => (
              <button
                key={s.symbol}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => handleSelectSuggestion(s)}
                className="w-full flex items-center justify-between px-3 py-2 hover:bg-bg-tertiary/80 transition-colors text-left border-b border-border/30 last:border-0"
              >
                <div>
                  <div className="text-xs font-medium text-text-primary">
                    {s.display}
                  </div>
                  <div className="text-[10px] text-text-muted">
                    Exp: {s.expiry}
                  </div>
                </div>
                <div className="text-right">
                  <div className="text-xs font-mono text-text-primary">
                    {formatINR(s.ltp)}
                  </div>
                  <div
                    className={`text-[10px] ${
                      s.type === "CE" ? "text-profit" : "text-loss"
                    }`}
                  >
                    {s.type}
                  </div>
                </div>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

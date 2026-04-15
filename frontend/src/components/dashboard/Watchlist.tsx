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
  short_name: string;
  segment: string;
  strike: number;
  type: string;
  ltp: number;
  expiry: string;
  lot_size: number;
}

interface WatchlistItem {
  symbol: string;
  display: string;
  segment: string;
}

const SEGMENT_BADGE: Record<string, { label: string; color: string }> = {
  EQ: { label: "EQ", color: "text-accent" },
  FUT: { label: "FUT", color: "text-warning" },
  OPT: { label: "OPT", color: "text-profit" },
};

export function Watchlist({ onOpenChart }: WatchlistProps) {
  const { prices, updatePrice } = useStore();
  const [watchlistItems, setWatchlistItems] = useState<WatchlistItem[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [suggestions, setSuggestions] = useState<SymbolResult[]>([]);
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [searching, setSearching] = useState(false);
  const debounceRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const inputRef = useRef<HTMLInputElement>(null);

  // Fetch prices for watchlist symbols
  const fetchWatchlistPrices = useCallback(
    async (items: WatchlistItem[]) => {
      if (items.length === 0) return;
      const symbols = items.map((i) => i.symbol);
      try {
        const priceData = await api.fetchBatchPrices(symbols);
        if (priceData) {
          for (const [symbol, data] of Object.entries(priceData)) {
            updatePrice(symbol, data as never);
          }
        }
      } catch {
        // API error — prices will show as "--"
      }
    },
    [updatePrice]
  );

  // Load watchlist from backend on mount
  useEffect(() => {
    async function loadWatchlist() {
      try {
        const data = await api.getWatchlist();
        const items = data.items.map((item) => ({
          symbol: item.symbol,
          display: item.display,
          segment: item.segment || "EQ",
        }));
        setWatchlistItems(items);
        // Fetch prices for loaded items
        await fetchWatchlistPrices(items);
      } catch {
        // Backend not available
      }
    }
    loadWatchlist();
  }, [fetchWatchlistPrices]);

  // Poll watchlist prices every 10 seconds
  useEffect(() => {
    if (watchlistItems.length === 0) return;
    const interval = setInterval(
      () => fetchWatchlistPrices(watchlistItems),
      10000
    );
    return () => clearInterval(interval);
  }, [watchlistItems, fetchWatchlistPrices]);

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
    async (result: SymbolResult) => {
      if (watchlistItems.some((c) => c.symbol === result.symbol)) {
        setInputValue("");
        setSuggestions([]);
        setShowSuggestions(false);
        return;
      }

      const newItem: WatchlistItem = {
        symbol: result.symbol,
        display: result.display,
        segment: result.segment,
      };

      // Add to backend watchlist
      try {
        await api.addToWatchlist({
          symbol: result.symbol,
          display: result.display,
          segment: result.segment,
          strike: result.strike || null,
          option_type: result.type || null,
          expiry: result.expiry,
        });
      } catch {
        // API error — still add locally for responsiveness
      }

      setWatchlistItems((prev) => [...prev, newItem]);

      // Fetch price immediately for the newly added item
      fetchWatchlistPrices([newItem]);

      setInputValue("");
      setSuggestions([]);
      setShowSuggestions(false);
    },
    [watchlistItems, fetchWatchlistPrices]
  );

  const handleRemoveItem = useCallback(async (symbol: string) => {
    setWatchlistItems((prev) => prev.filter((c) => c.symbol !== symbol));
    try {
      await api.removeFromWatchlist(symbol);
    } catch {
      // Ignore — already removed from UI
    }
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setShowSuggestions(false);
    }
  }, []);

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

        {/* Custom watchlist items from backend */}
        {watchlistItems.map((item) => {
          const price = prices[item.symbol];
          const changePct = price?.change_pct ?? 0;
          const badge = SEGMENT_BADGE[item.segment];

          return (
            <div
              key={item.symbol}
              className="flex items-center justify-between px-4 py-2.5 hover:bg-bg-tertiary/50 transition-colors group"
            >
              <button
                onClick={() => onOpenChart(item.symbol)}
                className="flex items-center gap-2 text-left flex-1 min-w-0"
              >
                <span className="text-sm font-medium text-text-primary truncate">
                  {item.display}
                </span>
                {badge && (
                  <span
                    className={`text-[9px] font-semibold px-1 py-0.5 rounded ${badge.color} bg-current/10 shrink-0`}
                  >
                    {badge.label}
                  </span>
                )}
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
                  onClick={() => handleRemoveItem(item.symbol)}
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

      {/* Search input with autocomplete */}
      <div className="px-3 py-2 border-t border-border relative">
        <input
          ref={inputRef}
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 200)}
          placeholder="Search: TCS, NIFTY 24000 CE..."
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
            {suggestions.map((s) => {
              const badge = SEGMENT_BADGE[s.segment];
              return (
                <button
                  key={s.symbol}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => handleSelectSuggestion(s)}
                  className="w-full flex items-center justify-between px-3 py-2 hover:bg-bg-tertiary/80 transition-colors text-left border-b border-border/30 last:border-0"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-1.5">
                      <span className="text-xs font-medium text-text-primary truncate">
                        {s.display}
                      </span>
                      {badge && (
                        <span
                          className={`text-[9px] font-semibold px-1 py-0.5 rounded ${badge.color} shrink-0`}
                        >
                          {badge.label}
                        </span>
                      )}
                    </div>
                    {s.expiry && (
                      <div className="text-[10px] text-text-muted">
                        Exp: {s.expiry}
                        {s.lot_size > 1 && ` · Lot: ${s.lot_size}`}
                      </div>
                    )}
                  </div>
                  {s.type && (
                    <div className="text-right shrink-0 ml-2">
                      <div
                        className={`text-[10px] font-semibold ${
                          s.type === "CE" ? "text-profit" : "text-loss"
                        }`}
                      >
                        {s.type}
                      </div>
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

"use client";

import { useState, useCallback, useEffect, useRef } from "react";
import { useStore } from "@/store";
import { SYMBOLS } from "@/lib/constants";
import { formatINR, pnlColor } from "@/lib/formatters";
import { api } from "@/lib/api";
import { subscribeSymbols } from "@/hooks/useWebSocket";

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
        // API error
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
        await fetchWatchlistPrices(items);
        // Subscribe custom symbols on WebSocket for real-time ticks
        const customSymbols = items.map((i) => i.symbol);
        if (customSymbols.length > 0) {
          subscribeSymbols(customSymbols);
        }
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
    if (inputValue.trim().length < 1) {
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
        // API error — still add locally
      }

      setWatchlistItems((prev) => [...prev, newItem]);
      fetchWatchlistPrices([newItem]);
      subscribeSymbols([newItem.symbol]);
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
      // Ignore
    }
  }, []);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      setShowSuggestions(false);
    }
  }, []);

  // Render a single watchlist row — unified for indices and custom items
  const renderRow = (
    symbol: string,
    displayName: string,
    segment?: string,
    removable?: boolean
  ) => {
    const price = prices[symbol];
    const changePct = price?.change_pct ?? 0;
    const change = price?.change ?? 0;

    return (
      <div
        key={symbol}
        className="flex items-center justify-between px-3 py-1.5 hover:bg-bg-tertiary/40 transition-colors group"
      >
        <button
          onClick={() => onOpenChart(symbol)}
          className="flex items-center gap-1.5 text-left flex-1 min-w-0"
        >
          <span className="text-xs font-medium text-text-primary truncate">
            {displayName}
          </span>
          {segment && segment !== "EQ" && segment !== "INDEX" && (
            <span className={`text-[10px] font-mono font-semibold px-0.5 rounded ${
              segment === "FUT" ? "text-warning" : "text-profit"
            }`}>
              {segment}
            </span>
          )}
        </button>
        <div className="flex items-center gap-2">
          <div className="text-right">
            <div className="text-xs font-mono text-text-primary">
              {price ? formatINR(price.ltp) : "--"}
            </div>
            {price && (
              <div className={`text-[10px] font-mono ${pnlColor(changePct)}`}>
                {changePct > 0 ? "+" : ""}
                {changePct.toFixed(2)}%
                <span className="text-text-muted ml-0.5">
                  ({change > 0 ? "+" : ""}{change?.toFixed(2) ?? "0"})
                </span>
              </div>
            )}
          </div>
          {removable && (
            <button
              onClick={() => handleRemoveItem(symbol)}
              className="opacity-0 group-hover:opacity-100 text-text-muted hover:text-loss transition-all w-4 h-4 flex items-center justify-center"
            >
              <svg className="w-2.5 h-2.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="rounded border border-border bg-bg-secondary">
      <div className="px-3 py-1.5 border-b border-border">
        <h2 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          Watchlist
        </h2>
      </div>

      <div className="max-h-[300px] overflow-y-auto divide-y divide-border/30">
        {/* Default indices */}
        {SYMBOLS.map((symbol) => renderRow(symbol, symbol))}

        {/* Custom watchlist items */}
        {watchlistItems.map((item) =>
          renderRow(item.symbol, item.display, item.segment, true)
        )}
      </div>

      {/* Search input */}
      <div className="px-2 py-1.5 border-t border-border relative">
        <input
          ref={inputRef}
          type="text"
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={handleKeyDown}
          onFocus={() => suggestions.length > 0 && setShowSuggestions(true)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 200)}
          placeholder="search symbols..."
          className="w-full text-xs font-mono bg-bg-tertiary border border-border rounded px-2 py-1 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/50 transition-colors"
        />
        {searching && (
          <div className="absolute right-4 top-2.5 text-[10px] text-text-muted font-mono">...</div>
        )}

        {/* Autocomplete dropdown */}
        {showSuggestions && suggestions.length > 0 && (
          <div className="absolute left-2 right-2 bottom-full mb-1 bg-bg-secondary border border-border rounded shadow-lg max-h-[200px] overflow-y-auto z-50">
            {suggestions.map((s) => (
              <button
                key={s.symbol}
                onMouseDown={(e) => e.preventDefault()}
                onClick={() => handleSelectSuggestion(s)}
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
        )}
      </div>
    </div>
  );
}

"use client";

import { useCallback, useEffect } from "react";
import { useStore } from "@/store";
import { useShallow } from "zustand/react/shallow";
import { SYMBOLS } from "@/lib/constants";
import { formatINR, pnlColor } from "@/lib/formatters";
import { api } from "@/lib/api";
import { SymbolSearchInput } from "@/components/shared/SymbolSearchInput";
import type { SymbolResult } from "@/components/shared/SymbolSearchInput";

interface WatchlistProps {
  onOpenChart: (symbol: string) => void;
}

interface WatchlistItem {
  symbol: string;
  display: string;
  segment: string;
}

export function Watchlist({ onOpenChart }: WatchlistProps) {
  // prices separated so price ticks don't cause unrelated store slices to
  // trigger re-renders across the whole component.
  const prices = useStore((s) => s.prices);
  const { updatePrice, watchlistItems, setWatchlistItems, addWatchlistItem, removeWatchlistItem } = useStore(
    useShallow((s) => ({
      updatePrice: s.updatePrice,
      watchlistItems: s.watchlistItems,
      setWatchlistItems: s.setWatchlistItems,
      addWatchlistItem: s.addWatchlistItem,
      removeWatchlistItem: s.removeWatchlistItem,
    }))
  );

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

  const handleSelectSuggestion = useCallback(
    async (result: SymbolResult) => {
      if (watchlistItems.some((c) => c.symbol === result.symbol)) {
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

      addWatchlistItem(newItem);
      fetchWatchlistPrices([newItem]);
    },
    [watchlistItems, fetchWatchlistPrices, addWatchlistItem]
  );

  const handleRemoveItem = useCallback(async (symbol: string) => {
    removeWatchlistItem(symbol);
    try {
      await api.removeFromWatchlist(symbol);
    } catch {
      // Ignore
    }
  }, [removeWatchlistItem]);

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

      <SymbolSearchInput onSelect={handleSelectSuggestion} placeholder="search symbols..." direction="down" />

      <div className="max-h-[300px] overflow-y-auto divide-y divide-border/30">
        {/* Default indices */}
        {SYMBOLS.map((symbol) => renderRow(symbol, symbol))}

        {/* Custom watchlist items */}
        {watchlistItems.map((item) =>
          renderRow(item.symbol, item.display, item.segment, true)
        )}
      </div>
    </div>
  );
}

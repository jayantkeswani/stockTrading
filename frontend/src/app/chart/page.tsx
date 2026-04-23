"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { PriceChart } from "@/components/charts/PriceChart";
import { useStore } from "@/store";
import { SYMBOLS, displaySymbol } from "@/lib/constants";
import { api } from "@/lib/api";

function ChartContent() {
  const searchParams = useSearchParams();
  const symbol = searchParams.get("symbol") || "NIFTY";
  const { selectedSymbol, setSelectedSymbol } = useStore();
  const [watchlistSymbols, setWatchlistSymbols] = useState<string[]>([]);

  useEffect(() => {
    setSelectedSymbol(symbol);
  }, [symbol, setSelectedSymbol]);

  useEffect(() => {
    api
      .getWatchlist()
      .then((data) => {
        const custom = data.items
          .map((i) => i.symbol)
          .filter((s) => !(SYMBOLS as readonly string[]).includes(s));
        setWatchlistSymbols(custom);
      })
      .catch(() => {});
  }, []);

  const allSymbols = [...SYMBOLS, ...watchlistSymbols];

  return (
    <div className="flex flex-col h-[calc(100vh-52px)]">
      {/* Symbol tabs — scrollable to handle watchlist items */}
      <div className="flex items-center gap-0.5 px-3 py-1 border-b border-border shrink-0 overflow-x-auto scrollbar-none">
        {allSymbols.map((sym) => (
          <button
            key={sym}
            onClick={() => setSelectedSymbol(sym)}
            className={`px-2 py-0.5 text-xs font-mono font-medium rounded transition-colors shrink-0 ${
              selectedSymbol === sym
                ? "bg-accent/15 text-accent border border-accent/30"
                : "text-text-muted hover:text-text-secondary hover:bg-bg-tertiary border border-transparent"
            }`}
          >
            {displaySymbol(sym)}
          </button>
        ))}
      </div>

      {/* Full-height chart */}
      <div className="flex-1 min-h-0">
        <PriceChart fullHeight />
      </div>
    </div>
  );
}

export default function ChartPage() {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center h-[calc(100vh-52px)]">
          <div className="text-text-muted text-xs font-mono">loading chart...</div>
        </div>
      }
    >
      <ChartContent />
    </Suspense>
  );
}

"use client";

import { Suspense, useEffect } from "react";
import { useSearchParams } from "next/navigation";
import { PriceChart } from "@/components/charts/PriceChart";
import { useStore } from "@/store";
import { SYMBOLS } from "@/lib/constants";

function ChartContent() {
  const searchParams = useSearchParams();
  const symbol = searchParams.get("symbol") || "NIFTY";
  const { selectedSymbol, setSelectedSymbol } = useStore();

  useEffect(() => {
    if (symbol) {
      setSelectedSymbol(symbol);
    }
  }, [symbol, setSelectedSymbol]);

  return (
    <div className="flex flex-col h-[calc(100vh-64px)]">
      {/* Symbol tabs */}
      <div className="flex items-center gap-1 px-4 py-2 border-b border-border shrink-0">
        {SYMBOLS.map((sym) => (
          <button
            key={sym}
            onClick={() => setSelectedSymbol(sym)}
            className={`px-3 py-1 text-xs font-medium rounded transition-colors ${
              selectedSymbol === sym
                ? "bg-accent/20 text-accent border border-accent/50"
                : "text-text-secondary hover:text-text-primary hover:bg-bg-tertiary border border-transparent"
            }`}
          >
            {sym}
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
        <div className="flex items-center justify-center h-[calc(100vh-64px)]">
          <div className="text-text-muted text-sm">Loading chart...</div>
        </div>
      }
    >
      <ChartContent />
    </Suspense>
  );
}

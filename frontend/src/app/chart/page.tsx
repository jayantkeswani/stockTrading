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
    <div className="flex flex-col h-[calc(100vh-52px)]">
      {/* Symbol tabs */}
      <div className="flex items-center gap-0.5 px-3 py-1 border-b border-border shrink-0">
        {SYMBOLS.map((sym) => (
          <button
            key={sym}
            onClick={() => setSelectedSymbol(sym)}
            className={`px-2 py-0.5 text-xs font-mono font-medium rounded transition-colors ${
              selectedSymbol === sym
                ? "bg-accent/15 text-accent border border-accent/30"
                : "text-text-muted hover:text-text-secondary hover:bg-bg-tertiary border border-transparent"
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
        <div className="flex items-center justify-center h-[calc(100vh-52px)]">
          <div className="text-text-muted text-xs font-mono">loading chart...</div>
        </div>
      }
    >
      <ChartContent />
    </Suspense>
  );
}

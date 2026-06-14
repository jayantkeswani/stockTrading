"use client";

import { useStore } from "@/store";
import { useShallow } from "zustand/react/shallow";
import { usePrices } from "@/hooks/usePrices";
import { SYMBOLS } from "@/lib/constants";
import { formatINR, pnlColor } from "@/lib/formatters";

export function SymbolSelector() {
  // Scoped to the 5 indices only — no re-render on unrelated symbol ticks.
  const prices = usePrices(SYMBOLS);
  const { selectedSymbol, setSelectedSymbol } = useStore(useShallow((s) => ({
    selectedSymbol: s.selectedSymbol,
    setSelectedSymbol: s.setSelectedSymbol,
  })));

  return (
    <div className="flex gap-2">
      {SYMBOLS.map((symbol) => {
        const price = prices[symbol];
        const isActive = selectedSymbol === symbol;
        const changePct = price?.change_pct ?? 0;

        return (
          <button
            key={symbol}
            onClick={() => setSelectedSymbol(symbol)}
            className={`px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border ${
              isActive
                ? "border-accent bg-accent/10 text-accent"
                : "border-border bg-bg-secondary text-text-secondary hover:border-border-hover hover:text-text-primary"
            }`}
          >
            <div>{symbol}</div>
            {price && (
              <div className={`text-[10px] font-mono ${pnlColor(changePct)}`}>
                {formatINR(price.ltp)} ({changePct > 0 ? "+" : ""}{changePct.toFixed(2)}%)
              </div>
            )}
          </button>
        );
      })}
    </div>
  );
}

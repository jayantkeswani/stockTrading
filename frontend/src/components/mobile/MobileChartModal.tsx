"use client";

import { useEffect } from "react";
import { PriceChart } from "@/components/charts/PriceChart";
import { useStore } from "@/store";
import { displaySymbol } from "@/lib/constants";

/**
 * Full-screen chart overlay for the mobile shell. Routes can't render a chart
 * page on phones (AppShell always swaps to MobileShell), so the watchlist opens
 * this overlay instead. Reuses the desktop TradingView `PriceChart` (driven by
 * the store's `selectedSymbol`, which we set on open). Pinned `data-theme="dark"`
 * because the chart's colors are dark-only — it reads as an intentional dark
 * chart sheet even when the mobile shell is in light mode (a dedicated light
 * chart theme is the separate full-app task). Used by: MobileShell.
 */
export function MobileChartModal({ symbol, display, onClose }: { symbol: string; display?: string; onClose: () => void }) {
  useEffect(() => {
    useStore.getState().setSelectedSymbol(symbol);
    document.body.style.overflow = "hidden";
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [symbol, onClose]);

  return (
    <div data-theme="dark" className="fixed inset-0 z-50 flex flex-col bg-bg-primary">
      <div className="shrink-0 flex items-center justify-between gap-2 px-4 py-3 pt-[calc(env(safe-area-inset-top)+0.5rem)] border-b border-border bg-bg-secondary">
        <span className="text-[15px] font-mono font-bold text-text-primary truncate">
          {display || displaySymbol(symbol)}
        </span>
        <button
          onClick={onClose}
          aria-label="Close chart"
          className="text-text-secondary hover:text-accent p-1.5 rounded border border-border bg-bg-tertiary shrink-0"
        >
          <span className="block text-[15px] leading-none">✕</span>
        </button>
      </div>
      <div className="flex-1 min-h-0">
        <PriceChart fullHeight />
      </div>
    </div>
  );
}

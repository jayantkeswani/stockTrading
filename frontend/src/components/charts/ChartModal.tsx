"use client";

import { useEffect, useCallback, useState } from "react";
import { PriceChart } from "./PriceChart";
import { useStore } from "@/store";
import { SYMBOLS, displaySymbol } from "@/lib/constants";
import { api } from "@/lib/api";

interface ChartModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialSymbol?: string;
}

export function ChartModal({ isOpen, onClose, initialSymbol }: ChartModalProps) {
  const selectedSymbol = useStore((s) => s.selectedSymbol);
  const [watchlistSymbols, setWatchlistSymbols] = useState<string[]>([]);

  useEffect(() => {
    if (isOpen && initialSymbol) {
      useStore.getState().setSelectedSymbol(initialSymbol);
    }
  }, [isOpen, initialSymbol]);

  // Fetch custom watchlist symbols to show alongside the 5 default indices
  useEffect(() => {
    if (!isOpen) return;
    api
      .getWatchlist()
      .then((data) => {
        const custom = data.items
          .map((i) => i.symbol)
          .filter((s) => !(SYMBOLS as readonly string[]).includes(s));
        setWatchlistSymbols(custom);
      })
      .catch(() => {});
  }, [isOpen]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    },
    [onClose]
  );

  useEffect(() => {
    if (isOpen) {
      document.addEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "hidden";
    }
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = "";
    };
  }, [isOpen, handleKeyDown]);

  if (!isOpen) return null;

  const handlePopOut = () => {
    window.open(`/chart?symbol=${encodeURIComponent(selectedSymbol)}`, "_blank");
    onClose();
  };

  const allSymbols = [...SYMBOLS, ...watchlistSymbols];

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div
        className="relative bg-bg-primary border border-border rounded shadow-2xl flex flex-col"
        style={{ width: "90vw", height: "85vh" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-3 py-2 border-b border-border shrink-0 min-w-0">
          {/* Symbol tabs — scrollable so watchlist items don't overflow */}
          <div className="flex gap-0.5 overflow-x-auto scrollbar-none flex-1 min-w-0 mr-2">
            {allSymbols.map((sym) => (
              <button
                key={sym}
                onClick={() => useStore.getState().setSelectedSymbol(sym)}
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

          {/* Actions */}
          <div className="flex items-center gap-2 shrink-0">
            <button
              onClick={handlePopOut}
              className="text-xs font-mono px-2 py-1 rounded text-text-muted hover:text-text-primary hover:bg-bg-tertiary transition-colors flex items-center gap-1 border border-border"
              title="Open in new tab"
            >
              <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
              Pop out
            </button>
            <button
              onClick={onClose}
              className="w-7 h-7 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-tertiary transition-colors"
              title="Close"
            >
              <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
        </div>

        {/* Chart body */}
        <div className="flex-1 min-h-0">
          <PriceChart fullHeight />
        </div>
      </div>
    </div>
  );
}

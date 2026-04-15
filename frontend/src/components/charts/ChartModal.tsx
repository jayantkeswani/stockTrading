"use client";

import { useEffect, useCallback } from "react";
import { PriceChart } from "./PriceChart";
import { useStore } from "@/store";
import { SYMBOLS } from "@/lib/constants";

interface ChartModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialSymbol?: string;
}

export function ChartModal({ isOpen, onClose, initialSymbol }: ChartModalProps) {
  const { selectedSymbol, setSelectedSymbol } = useStore();

  useEffect(() => {
    if (isOpen && initialSymbol) {
      setSelectedSymbol(initialSymbol);
    }
  }, [isOpen, initialSymbol, setSelectedSymbol]);

  // Close on Escape key
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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/70 backdrop-blur-sm"
        onClick={onClose}
      />

      {/* Modal */}
      <div
        className="relative bg-bg-primary border border-border rounded-xl shadow-2xl flex flex-col"
        style={{ width: "90vw", height: "85vh" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
          {/* Symbol tabs */}
          <div className="flex gap-1">
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

          {/* Actions */}
          <div className="flex items-center gap-2">
            <button
              onClick={handlePopOut}
              className="text-xs px-3 py-1.5 rounded text-text-secondary hover:text-text-primary hover:bg-bg-tertiary transition-colors flex items-center gap-1 border border-border"
              title="Open in new tab"
            >
              <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
              </svg>
              Pop out
            </button>
            <button
              onClick={onClose}
              className="w-8 h-8 rounded flex items-center justify-center text-text-muted hover:text-text-primary hover:bg-bg-tertiary transition-colors"
              title="Close"
            >
              <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
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

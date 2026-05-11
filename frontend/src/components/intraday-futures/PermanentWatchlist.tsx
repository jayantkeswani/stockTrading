"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { SymbolSearchInput } from "@/components/shared/SymbolSearchInput";
import type { SymbolResult } from "@/components/shared/SymbolSearchInput";

export function PermanentWatchlist() {
  const [symbols, setSymbols] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);

  useEffect(() => {
    api.getPermanentWatchlist()
      .then((data) => setSymbols(data.symbols))
      .catch(() => {/* silent */})
      .finally(() => setLoading(false));
  }, []);

  const handleAdd = async (result: SymbolResult) => {
    setError(null);
    const sym = result.short_name;
    if (symbols.includes(sym)) return;
    setSymbols((prev) => [...prev, sym]);
    try {
      const data = await api.addToPermanentWatchlist(sym);
      setSymbols(data.symbols);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Symbol not eligible for F&O watchlist");
      setSymbols((prev) => prev.filter((s) => s !== sym));
    }
  };

  const handleRemove = async (sym: string) => {
    setRemoving(sym);
    setSymbols((prev) => prev.filter((s) => s !== sym));
    try {
      const data = await api.removeFromPermanentWatchlist(sym);
      setSymbols(data.symbols);
    } catch {
      // silent — optimistic removal stands
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="border border-border rounded bg-bg-secondary">
      <div className="px-3 py-1.5 border-b border-border">
        <span className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          Permanent Watchlist
        </span>
      </div>

      <div className="flex flex-wrap gap-1.5 px-3 py-2">
        {loading ? (
          <span className="text-[11px] text-text-muted font-mono">...</span>
        ) : symbols.length === 0 ? (
          <span className="text-[11px] text-text-muted font-mono">No stocks pinned yet</span>
        ) : (
          symbols.map((sym) => (
            <span
              key={sym}
              className={`flex items-center gap-1 text-xs font-mono bg-bg-tertiary border border-border rounded px-1.5 py-0.5 text-text-primary transition-opacity${removing === sym ? " opacity-40" : ""}`}
            >
              {sym}
              <button
                onClick={() => handleRemove(sym)}
                className="text-text-muted hover:text-loss ml-0.5 text-[10px]"
                aria-label={`Remove ${sym}`}
              >
                ×
              </button>
            </span>
          ))
        )}
      </div>

      {error && (
        <p className="text-[10px] text-loss font-mono px-3 pb-1">{error}</p>
      )}

      <SymbolSearchInput segmentFilter="EQ" onSelect={handleAdd} placeholder="add F&O stock..." />

      <p className="text-[10px] text-text-muted font-mono px-3 pb-2 italic">
        Takes effect from next morning screener run
      </p>
    </div>
  );
}

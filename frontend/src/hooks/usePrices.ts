import { useShallow } from "zustand/react/shallow";
import { useStore } from "@/store";
import type { PriceData } from "@/lib/types";

/**
 * Subscribe to live prices for ONLY the given symbols.
 *
 * Returns a map keyed by the requested symbols (missing ones omitted). Backed by
 * `useShallow`, so the component re-renders only when one of *these* symbols ticks
 * — not on every flush of the shared `prices` map (the backend broadcasts every
 * symbol; the store replaces the whole `prices` object reference on each 500ms
 * batch). Prefer this over `useStore((s) => s.prices)` whenever a component reads
 * a known, bounded set of symbols.
 *
 * Unchanged symbols keep their `PriceData` reference across flushes (the store
 * only reassigns entries whose LTP changed), so the shallow compare correctly
 * skips the re-render when none of the requested symbols moved. The returned
 * object identity is stable in that case too — safe as a `useMemo` dependency.
 */
export function usePrices(symbols: readonly string[]): Record<string, PriceData> {
  return useStore(
    useShallow((s) => {
      const out: Record<string, PriceData> = {};
      for (const sym of symbols) {
        const p = s.prices[sym];
        if (p) out[sym] = p;
      }
      return out;
    }),
  );
}

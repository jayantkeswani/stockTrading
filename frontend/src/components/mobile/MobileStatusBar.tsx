"use client";

import { useEffect } from "react";
import { useStore } from "@/store";
import { useShallow } from "zustand/react/shallow";
import { api } from "@/lib/api";
import type { MarketStatus } from "@/lib/types";

/**
 * Thin status strip under the mobile header: market open/closed (+ DEAD ZONE),
 * the live NIFTY intraday bias, and India VIX — the phone equivalent of the
 * desktop `Header` top bar. The desktop Header polls `getMarketStatus` (and
 * isn't rendered on mobile), so this component owns that poll for the phone.
 * `intradayBias` is hydrated from the cache once on mount (persisted across
 * refreshes, stays shown after close) and kept live via the WebSocket
 * (`market:bias_update`). Isolated so its 15s poll + bias ticks re-render only
 * this strip. Used by: MobileShell.
 */
export function MobileStatusBar() {
  const { marketStatus, setMarketStatus, intradayBias, setIntradayBias } = useStore(useShallow((s) => ({
    marketStatus: s.marketStatus,
    setMarketStatus: s.setMarketStatus,
    intradayBias: s.intradayBias,
    setIntradayBias: s.setIntradayBias,
  })));

  useEffect(() => {
    let stopped = false;
    async function fetchStatus() {
      try {
        const m = (await api.getMarketStatus()) as MarketStatus;
        if (!stopped) setMarketStatus(m);
      } catch { /* API not running */ }
    }
    fetchStatus();
    const id = setInterval(fetchStatus, 15_000);
    return () => { stopped = true; clearInterval(id); };
  }, [setMarketStatus]);

  // Hydrate the NIFTY bias from the cache once on mount; WS keeps it live.
  useEffect(() => {
    let stopped = false;
    api.getIntradayBias().then((b) => {
      if (!stopped && b) setIntradayBias(b);
    }).catch(() => { /* API not running */ });
    return () => { stopped = true; };
  }, [setIntradayBias]);

  const biasColor = intradayBias?.bias === "BULLISH" ? "text-profit"
    : intradayBias?.bias === "BEARISH" ? "text-loss" : "text-text-muted";

  return (
    <div className="shrink-0 flex items-center gap-3 px-4 py-1 border-b border-border bg-bg-secondary text-[10px] font-mono overflow-x-auto">
      <span className={`font-medium shrink-0 ${marketStatus?.is_open ? "text-profit" : "text-text-muted"}`}>
        {marketStatus ? (marketStatus.is_open ? "MKT OPEN" : "MKT CLOSED") : "MKT —"}
      </span>
      {marketStatus?.in_dead_zone && <span className="text-warning shrink-0">DEAD ZONE</span>}
      {intradayBias && (
        <span className={`shrink-0 ${biasColor}`}>
          NIFTY {intradayBias.bias} {intradayBias.strength}
        </span>
      )}
      {marketStatus?.india_vix != null && (
        <span className="text-text-muted shrink-0 ml-auto">
          VIX <span className="text-text-secondary">{marketStatus.india_vix.toFixed(2)}</span>
        </span>
      )}
    </div>
  );
}

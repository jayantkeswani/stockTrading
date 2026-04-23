"use client";

import { useEffect, useState, useMemo } from "react";
import { api } from "@/lib/api";
import { startOfMonthIST, endOfMonthIST, isoDateIST } from "@/lib/formatters";
import type { Trade } from "@/lib/types";
import { PeriodFilter, type Period } from "@/components/trades/PeriodFilter";
import { SummaryStrip } from "@/components/trades/SummaryStrip";
import { PnLHeatmap } from "@/components/trades/PnLHeatmap";
import { TradesTable } from "@/components/trades/TradesTable";

function defaultPeriod(): Period {
  const now = new Date();
  return { start: startOfMonthIST(now), end: endOfMonthIST(now), label: "This Month" };
}

export default function TradesPage() {
  const [period, setPeriod] = useState<Period>(defaultPeriod);
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  useEffect(() => {
    setSelectedDay(null);
    setLoading(true);
    let cancelled = false;
    async function load() {
      try {
        const data = (await api.getTrades({
          entry_since: period.start.toISOString(),
          entry_until: period.end.toISOString(),
          limit: 1000,
        })) as Trade[];
        if (!cancelled) setTrades(data);
      } catch {
        if (!cancelled) setTrades([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [period]);

  const dailyPnL = useMemo(() => {
    const map = new Map<string, number>();
    for (const t of trades) {
      if (t.status !== "CLOSED" || t.pnl == null) continue;
      const key = isoDateIST(new Date(t.entry_time));
      map.set(key, (map.get(key) ?? 0) + Number(t.pnl));
    }
    return map;
  }, [trades]);

  const displayedTrades = useMemo(() => {
    if (!selectedDay) return trades;
    return trades.filter((t) => isoDateIST(new Date(t.entry_time)) === selectedDay);
  }, [trades, selectedDay]);

  const periodLabel = `${period.start.toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata", day: "2-digit", month: "short",
  })} — ${period.end.toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric",
  })}`;

  return (
    <div className="space-y-2">
      {/* Header: period pills + date range */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <PeriodFilter value={period} onChange={setPeriod} />
        <span className="text-[9px] font-mono text-text-muted/40 tracking-wider">
          {periodLabel}
        </span>
      </div>

      <SummaryStrip trades={trades} dailyPnL={dailyPnL} />

      <PnLHeatmap
        period={period}
        dailyPnL={dailyPnL}
        selectedDay={selectedDay}
        onSelectDay={setSelectedDay}
      />

      {/* Trades table */}
      <div className="rounded border border-border bg-bg-secondary overflow-hidden">
        {selectedDay && (
          <div className="px-3 py-1 border-b border-border/30 flex items-center gap-2 bg-bg-tertiary/30">
            <span className="text-[8px] font-mono uppercase tracking-wider text-text-muted/50">Day</span>
            <span className="text-[9px] font-mono text-accent">{selectedDay}</span>
            <span className="text-[8px] font-mono text-text-muted/40">
              · {displayedTrades.length} trade{displayedTrades.length !== 1 ? "s" : ""}
            </span>
            <button
              onClick={() => setSelectedDay(null)}
              className="ml-auto text-[8px] font-mono text-text-muted/40 hover:text-text-muted transition-colors"
            >
              ✕ show all
            </button>
          </div>
        )}
        <TradesTable trades={displayedTrades} loading={loading} />
      </div>
    </div>
  );
}

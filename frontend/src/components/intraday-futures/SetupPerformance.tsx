"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatINR } from "@/lib/formatters";
import type { S5SetupPerformance } from "@/lib/types";

const SETUP_LABELS: Record<string, string> = {
  ORB: "ORB",
  VWAP_BOUNCE: "VWAP Bounce",
  PDH_PDL: "PDH/PDL",
  GAP_CONTINUATION: "Gap Cont.",
};

export function SetupPerformance({ date }: { date: string | null }) {
  const [data, setData] = useState<S5SetupPerformance | null>(null);
  const [collapsed, setCollapsed] = useState(false);

  useEffect(() => {
    api
      .getIntradayFuturesSetupPerformance(date ?? undefined)
      .then(setData)
      .catch(() => setData(null));
  }, [date]);

  const hasData = data && data.overall.trades > 0;

  return (
    <div className="border border-border rounded bg-bg-secondary">
      <button
        onClick={() => setCollapsed(!collapsed)}
        className="w-full flex items-center justify-between px-3 py-1.5 border-b border-border hover:bg-bg-tertiary/30 transition-colors"
      >
        <div className="flex items-center gap-2">
          <span className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
            Setup Performance ({data?.period.days ?? 5}d)
          </span>
          {hasData && (
            <span
              className={`text-[10px] font-mono px-1.5 py-px rounded ${
                data.overall.win_rate >= 60
                  ? "bg-profit/15 text-profit"
                  : data.overall.win_rate >= 45
                  ? "bg-accent/15 text-accent"
                  : "bg-loss/15 text-loss"
              }`}
            >
              {data.overall.win_rate}%
            </span>
          )}
        </div>
        <span className="text-[10px] font-mono text-text-muted">
          {collapsed ? "▸" : "▾"}
        </span>
      </button>

      {!collapsed && (
        <div className="px-3 py-2">
          {!hasData ? (
            <p className="text-xs font-mono text-text-muted text-center py-2">
              no trades yet
            </p>
          ) : (
            <div className="space-y-1.5">
              {Object.entries(data.setups).map(([key, stats]) => {
                const barWidth = Math.round(stats.win_rate);
                const barColor =
                  stats.win_rate >= 60
                    ? "bg-profit"
                    : stats.win_rate >= 45
                    ? "bg-accent"
                    : "bg-loss";
                return (
                  <div key={key} className="space-y-0.5">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] font-mono text-accent">
                        {SETUP_LABELS[key] || key}
                      </span>
                      <div className="flex items-center gap-2 text-[10px] font-mono">
                        <span className="text-text-muted">
                          {stats.wins}W {stats.losses}L
                        </span>
                        <span
                          className={
                            stats.net_pnl >= 0 ? "text-profit" : "text-loss"
                          }
                        >
                          {formatINR(stats.net_pnl)}
                        </span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="flex-1 h-1 bg-border/40 rounded-full overflow-hidden">
                        <div
                          className={`h-full ${barColor} rounded-full`}
                          style={{ width: `${barWidth}%` }}
                        />
                      </div>
                      <span className="text-[9px] font-mono text-text-muted w-8 text-right">
                        {stats.win_rate}%
                      </span>
                    </div>
                  </div>
                );
              })}

              {/* Overall summary */}
              <div className="border-t border-border/30 pt-1.5 mt-1.5 flex items-center justify-between text-[10px] font-mono">
                <span className="text-text-muted uppercase tracking-wider">
                  Total
                </span>
                <div className="flex items-center gap-3">
                  <span className="text-text-secondary">
                    {data.overall.trades} trades
                  </span>
                  <span className="text-text-muted">
                    {data.overall.wins}W {data.overall.losses}L
                  </span>
                  <span
                    className={
                      data.overall.net_pnl >= 0 ? "text-profit" : "text-loss"
                    }
                  >
                    {formatINR(data.overall.net_pnl)}
                  </span>
                </div>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

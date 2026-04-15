"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatINR, formatPercent, formatTime, formatDate, pnlColor } from "@/lib/formatters";
import { STRATEGY_LABELS, STATUS_COLORS } from "@/lib/constants";
import type { Trade } from "@/lib/types";

export default function TradesPage() {
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const data = (await api.getTrades({ limit: 100 })) as Trade[];
        setTrades(data);
      } catch {
        // API not running yet
      }
      setLoading(false);
    }
    load();
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Trade History</h1>

      <div className="rounded-lg border border-border bg-bg-secondary overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-text-muted">Loading...</div>
        ) : trades.length === 0 ? (
          <div className="p-8 text-center text-text-muted">No trades yet</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-xs text-text-muted uppercase tracking-wider bg-bg-tertiary/50">
                  <th className="text-left px-4 py-2">Date</th>
                  <th className="text-left px-4 py-2">Symbol</th>
                  <th className="text-left px-4 py-2">Type</th>
                  <th className="text-right px-4 py-2">Entry</th>
                  <th className="text-right px-4 py-2">Exit</th>
                  <th className="text-right px-4 py-2">P&L</th>
                  <th className="text-left px-4 py-2">Strategy</th>
                  <th className="text-left px-4 py-2">Exit Reason</th>
                  <th className="text-left px-4 py-2">Status</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade) => (
                  <tr
                    key={trade.id}
                    className="border-t border-border/50 hover:bg-bg-tertiary/30"
                  >
                    <td className="px-4 py-2.5 text-text-secondary text-xs font-mono">
                      {formatDate(trade.entry_time)}
                      <br />
                      {formatTime(trade.entry_time)}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="font-medium">{trade.symbol}</span>
                      <span
                        className={`ml-1 text-xs ${
                          trade.option_type === "CE" ? "text-profit" : "text-loss"
                        }`}
                      >
                        {trade.strike_price} {trade.option_type}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs">{trade.side}</td>
                    <td className="px-4 py-2.5 text-right font-mono">
                      {formatINR(trade.entry_price)}
                    </td>
                    <td className="px-4 py-2.5 text-right font-mono">
                      {trade.exit_price ? formatINR(trade.exit_price) : "—"}
                    </td>
                    <td
                      className={`px-4 py-2.5 text-right font-mono font-semibold ${pnlColor(
                        trade.pnl ?? 0
                      )}`}
                    >
                      {trade.pnl != null ? (
                        <>
                          {formatINR(trade.pnl)}
                          <div className="text-xs">
                            {formatPercent(trade.pnl_percent ?? 0)}
                          </div>
                        </>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="px-4 py-2.5">
                      <span className="text-xs px-1.5 py-0.5 rounded bg-accent/20 text-accent">
                        {STRATEGY_LABELS[trade.strategy_name] || trade.strategy_name}
                      </span>
                    </td>
                    <td className="px-4 py-2.5 text-xs text-text-secondary">
                      {trade.exit_reason || "—"}
                    </td>
                    <td className="px-4 py-2.5">
                      <span
                        className={`text-xs px-1.5 py-0.5 rounded ${
                          STATUS_COLORS[trade.status] || ""
                        }`}
                      >
                        {trade.status}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

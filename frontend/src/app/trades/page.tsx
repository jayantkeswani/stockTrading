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
    <div className="space-y-2">
      <h1 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
        Trade History
      </h1>

      <div className="rounded border border-border bg-bg-secondary overflow-hidden">
        {loading ? (
          <div className="px-3 py-6 text-center text-text-muted text-[10px] font-mono">loading...</div>
        ) : trades.length === 0 ? (
          <div className="px-3 py-6 text-center text-text-muted text-[10px] font-mono">no trades yet</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-[11px]">
              <thead>
                <tr className="text-[9px] text-text-muted uppercase font-mono tracking-wider bg-bg-tertiary/40">
                  <th className="text-left px-3 py-1.5">Date</th>
                  <th className="text-left px-3 py-1.5">Symbol</th>
                  <th className="text-left px-3 py-1.5">Type</th>
                  <th className="text-right px-3 py-1.5">Entry</th>
                  <th className="text-right px-3 py-1.5">Exit</th>
                  <th className="text-right px-3 py-1.5">P&L</th>
                  <th className="text-left px-3 py-1.5">Strategy</th>
                  <th className="text-left px-3 py-1.5">Exit Reason</th>
                  <th className="text-left px-3 py-1.5">Status</th>
                </tr>
              </thead>
              <tbody>
                {trades.map((trade) => (
                  <tr
                    key={trade.id}
                    className="border-t border-border/30 hover:bg-bg-tertiary/30"
                  >
                    <td className="px-3 py-1.5 text-text-muted text-[10px] font-mono">
                      {formatDate(trade.entry_time)}
                      <br />
                      {formatTime(trade.entry_time)}
                    </td>
                    <td className="px-3 py-1.5 font-mono">
                      <span className="font-medium">{trade.symbol}</span>
                      <span
                        className={`ml-1 text-[9px] ${
                          trade.option_type === "CE" ? "text-profit" : "text-loss"
                        }`}
                      >
                        {trade.strike_price} {trade.option_type}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-[10px] font-mono text-text-secondary">{trade.side}</td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      {formatINR(trade.entry_price)}
                    </td>
                    <td className="px-3 py-1.5 text-right font-mono">
                      {trade.exit_price ? formatINR(trade.exit_price) : "\u2014"}
                    </td>
                    <td
                      className={`px-3 py-1.5 text-right font-mono font-medium ${pnlColor(
                        trade.pnl ?? 0
                      )}`}
                    >
                      {trade.pnl != null ? (
                        <>
                          {formatINR(trade.pnl)}
                          <div className="text-[9px]">
                            {formatPercent(trade.pnl_percent ?? 0)}
                          </div>
                        </>
                      ) : (
                        "\u2014"
                      )}
                    </td>
                    <td className="px-3 py-1.5">
                      <span className="text-[9px] font-mono px-1 py-px rounded bg-accent/10 text-accent">
                        {STRATEGY_LABELS[trade.strategy_name] || trade.strategy_name}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-[10px] font-mono text-text-muted">
                      {trade.exit_reason || "\u2014"}
                    </td>
                    <td className="px-3 py-1.5">
                      <span
                        className={`text-[9px] font-mono px-1 py-px rounded ${
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

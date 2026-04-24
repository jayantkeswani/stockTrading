"use client";

import { formatINR, formatPercent, formatTime, formatDate, pnlColor } from "@/lib/formatters";
import { STRATEGY_LABELS, STATUS_COLORS } from "@/lib/constants";
import type { Trade } from "@/lib/types";

interface Props {
  trades: Trade[];
  loading: boolean;
  showSource?: boolean;
}

export function TradesTable({ trades, loading, showSource = false }: Props) {
  if (loading) {
    return (
      <div className="px-3 py-6 text-center text-text-muted text-xs font-mono">loading...</div>
    );
  }

  if (trades.length === 0) {
    return (
      <div className="px-3 py-6 text-center text-text-muted text-xs font-mono">
        no trades in this period
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-[10px] text-text-muted uppercase font-mono tracking-wider bg-bg-tertiary/40">
            <th className="text-left px-3 py-1.5">Date / Time</th>
            <th className="text-left px-3 py-1.5">Symbol</th>
            <th className="text-left px-3 py-1.5">Type</th>
            <th className="text-right px-3 py-1.5">Entry</th>
            <th className="text-right px-3 py-1.5">Exit</th>
            <th className="text-right px-3 py-1.5">P&amp;L</th>
            <th className="text-left px-3 py-1.5">Strategy</th>
            <th className="text-left px-3 py-1.5">Exit Reason</th>
            <th className="text-left px-3 py-1.5">Status</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((trade) => (
            <tr key={trade.id} className="border-t border-border/30 hover:bg-bg-tertiary/30">
              <td className="px-3 py-1.5 text-text-muted text-xs font-mono">
                {formatDate(trade.entry_time)}
                <br />
                {formatTime(trade.entry_time)}
              </td>
              <td className="px-3 py-1.5 font-mono">
                <span className="font-medium">{trade.symbol}</span>
                {trade.option_type && (
                  <span
                    className={`ml-1 text-[10px] ${
                      trade.option_type === "CE" ? "text-profit" : "text-loss"
                    }`}
                  >
                    {trade.strike_price} {trade.option_type}
                  </span>
                )}
              </td>
              <td className="px-3 py-1.5 text-xs font-mono text-text-secondary">{trade.side}</td>
              <td className="px-3 py-1.5 text-right font-mono">{formatINR(trade.entry_price)}</td>
              <td className="px-3 py-1.5 text-right font-mono">
                {trade.exit_price ? formatINR(trade.exit_price) : "—"}
              </td>
              <td className={`px-3 py-1.5 text-right font-mono font-medium ${pnlColor(trade.pnl ?? 0)}`}>
                {trade.pnl != null ? (
                  <>
                    {formatINR(trade.pnl)}
                    <div className="text-[10px]">{formatPercent(trade.pnl_percent ?? 0)}</div>
                  </>
                ) : (
                  "—"
                )}
              </td>
              <td className="px-3 py-1.5">
                <span className="text-[10px] font-mono px-1 py-px rounded bg-accent/10 text-accent">
                  {STRATEGY_LABELS[trade.strategy_name] || trade.strategy_name}
                </span>
              </td>
              <td className="px-3 py-1.5 text-xs font-mono text-text-muted">
                {trade.exit_reason || "—"}
              </td>
              <td className="px-3 py-1.5">
                <span
                  className={`text-[10px] font-mono px-1 py-px rounded ${
                    STATUS_COLORS[trade.status] || ""
                  }`}
                >
                  {trade.status}
                </span>
              </td>
              {showSource && (
                <td className="px-3 py-1.5">
                  <span className="text-[9px] font-mono px-1 py-px rounded bg-purple-500/15 text-purple-400">
                    {trade.source}
                  </span>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

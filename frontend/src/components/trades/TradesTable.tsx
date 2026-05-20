"use client";

import { formatINR, formatPercent, formatTime, formatDate, pnlColor } from "@/lib/formatters";
import { STRATEGY_LABELS, STATUS_COLORS } from "@/lib/constants";
import type { Trade } from "@/lib/types";

interface Props {
  trades: Trade[];
  loading: boolean;
  showSource?: boolean;
  showSignalData?: boolean;
  simLots?: number | null;
  showNetPnL?: boolean;
}

function confidenceColor(conf: number | null): string {
  if (conf == null) return "text-text-muted";
  if (conf >= 80) return "text-profit";
  if (conf >= 70) return "text-accent";
  if (conf >= 50) return "text-text-secondary";
  return "text-loss";
}

export function TradesTable({ trades, loading, showSource = false, showSignalData = false, simLots = null, showNetPnL = false }: Props) {
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
            <th className="text-right px-3 py-1.5">
              P&amp;L
              {showNetPnL && <span className="normal-case font-normal text-accent/60 ml-1">net</span>}
              {simLots != null && (
                <span className="normal-case font-normal text-accent/60 ml-1">sim {simLots}L</span>
              )}
            </th>
            <th className="text-left px-3 py-1.5">Strategy</th>
            {showSignalData && <th className="text-right px-3 py-1.5">Conf</th>}
            {showSignalData && <th className="text-left px-3 py-1.5">AI</th>}
            <th className="text-left px-3 py-1.5">Exit Reason</th>
            <th className="text-left px-3 py-1.5">Status</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((trade) => (
            <tr key={trade.id} className="border-t border-border/30 hover:bg-bg-tertiary/30 hover:relative hover:z-10">
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
              <td className={`px-3 py-1.5 text-right font-mono font-medium ${pnlColor(
                showNetPnL && trade.net_pnl != null ? trade.net_pnl : (trade.pnl ?? 0)
              )}`}>
                {trade.pnl != null ? (
                  <div className="flex items-center justify-end gap-0.5">
                    <div>
                      {formatINR(showNetPnL && trade.net_pnl != null ? trade.net_pnl : trade.pnl)}
                      <div className="text-[10px]">{formatPercent(trade.pnl_percent ?? 0)}</div>
                    </div>
                    {showNetPnL && trade.charges_json && (
                      <div className="relative group inline-block ml-1 cursor-help text-text-muted/40 text-[9px]">
                        i
                        <div className="absolute top-full right-0 z-50 hidden group-hover:block
                                        bg-bg-elevated border border-border rounded p-2 text-[9px]
                                        font-mono w-44 shadow-lg whitespace-nowrap text-text-secondary font-normal text-left mt-1">
                          <div>Brokerage: {formatINR(trade.charges_json.brokerage)}</div>
                          <div>STT: {formatINR(trade.charges_json.stt)}</div>
                          <div>Exchange: {formatINR(trade.charges_json.exchange_txn)}</div>
                          <div>GST: {formatINR(trade.charges_json.gst)}</div>
                          <div>SEBI: {formatINR(trade.charges_json.sebi_charges)}</div>
                          <div>Stamp: {formatINR(trade.charges_json.stamp_duty)}</div>
                          <div className="border-t border-border/40 mt-1 pt-1 text-text-primary">
                            Total: {formatINR(trade.charges_json.total)}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                ) : (
                  "—"
                )}
              </td>
              <td className="px-3 py-1.5">
                <span className="text-[10px] font-mono px-1 py-px rounded bg-accent/10 text-accent">
                  {STRATEGY_LABELS[trade.strategy_name] || trade.strategy_name}
                </span>
              </td>
              {showSignalData && (
                <td className={`px-3 py-1.5 text-right font-mono text-xs ${confidenceColor(trade.signal_confidence)}`}>
                  {trade.signal_confidence != null ? Math.round(Number(trade.signal_confidence)) : "—"}
                </td>
              )}
              {showSignalData && (
                <td className="px-3 py-1.5">
                  {trade.signal_ai_action ? (
                    <span className={`text-[9px] font-mono px-1 py-px rounded ${
                      trade.signal_ai_action === "PROCEED"
                        ? "bg-profit/10 text-profit"
                        : trade.signal_ai_action === "SKIP"
                        ? "bg-loss/10 text-loss"
                        : "bg-warning/10 text-warning"
                    }`}>
                      {trade.signal_ai_action}
                    </span>
                  ) : (
                    <span className="text-text-muted text-xs font-mono">—</span>
                  )}
                </td>
              )}
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

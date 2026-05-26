"use client";

import { formatINR, pnlColor } from "@/lib/formatters";
import type { Trade } from "@/lib/types";

interface Props {
  trades: Trade[];
  dailyPnL: Map<string, number>;
  showNetPnL?: boolean;
  peakMargin?: number;
  label?: string;
}

function tradePnl(t: Trade, showNet: boolean): number {
  if (showNet && t.net_pnl != null) return Number(t.net_pnl);
  return Number(t.pnl);
}

export function SummaryStrip({ trades, dailyPnL, showNetPnL = false, peakMargin, label }: Props) {
  const closed = trades.filter((t) => t.status === "CLOSED" && t.pnl != null);
  const openCount = trades.filter((t) => t.status === "OPEN").length;
  const totalPnl = closed.reduce((s, t) => s + tradePnl(t, showNetPnL), 0);
  const winners = closed.filter((t) => tradePnl(t, showNetPnL) > 0);
  const winRate = closed.length > 0 ? (winners.length / closed.length) * 100 : 0;
  const avgPnl = closed.length > 0 ? totalPnl / closed.length : 0;

  const dayValues = Array.from(dailyPnL.values());
  const bestDay = dayValues.length > 0 ? Math.max(...dayValues) : 0;
  const worstDay = dayValues.length > 0 ? Math.min(...dayValues) : 0;

  const losers = closed.filter((t) => tradePnl(t, showNetPnL) < 0);
  const grossProfit = winners.reduce((s, t) => s + tradePnl(t, showNetPnL), 0);
  const grossLoss = Math.abs(losers.reduce((s, t) => s + tradePnl(t, showNetPnL), 0));
  // No losses yet → show win rate as %; once losses exist → show gross profit / gross loss as ratio
  const profitFactor =
    closed.length === 0
      ? "—"
      : grossLoss === 0
      ? `${winRate.toFixed(0)}%`
      : `${(grossProfit / grossLoss).toFixed(2)}`;

  return (
    <div className={`flex items-center gap-5 px-3 py-1.5 rounded border ${label ? "border-accent/30 bg-accent/5" : "border-border bg-bg-secondary"} flex-wrap`}>
      <div className="flex items-center gap-2">
        {label && (
          <span className="text-[10px] font-mono font-semibold uppercase tracking-widest text-accent">{label}</span>
        )}
        <span className="text-[10px] text-text-muted font-mono uppercase tracking-wider">P&amp;L</span>
        <span className={`text-sm font-bold font-mono ${pnlColor(totalPnl)}`}>
          {formatINR(totalPnl)}
        </span>
      </div>

      <div className="w-px h-4 bg-border" />

      <div className="flex items-center gap-2">
        <span className="text-[10px] text-text-muted font-mono uppercase tracking-wider">Trades</span>
        <span className="text-xs font-mono text-text-primary">{closed.length}</span>
        {openCount > 0 && (
          <span className="text-[10px] font-mono text-accent">+{openCount} open</span>
        )}
      </div>

      <div className="w-px h-4 bg-border" />

      <div className="flex items-center gap-2">
        <span className="text-[10px] text-text-muted font-mono uppercase tracking-wider">Win Rate</span>
        <span className="text-xs font-mono text-text-primary">{winRate.toFixed(1)}%</span>
        <span className="text-[10px] font-mono text-text-muted">
          {winners.length}W / {closed.length - winners.length}L
        </span>
      </div>

      <div className="w-px h-4 bg-border" />

      <div className="flex items-center gap-2">
        <span className="text-[10px] text-text-muted font-mono uppercase tracking-wider">Best Day</span>
        <span className={`text-xs font-mono font-medium ${pnlColor(bestDay)}`}>
          {formatINR(bestDay)}
        </span>
      </div>

      <div className="w-px h-4 bg-border" />

      <div className="flex items-center gap-2">
        <span className="text-[10px] text-text-muted font-mono uppercase tracking-wider">Worst Day</span>
        <span className={`text-xs font-mono font-medium ${pnlColor(worstDay)}`}>
          {formatINR(worstDay)}
        </span>
      </div>

      <div className="w-px h-4 bg-border" />

      <div className="flex items-center gap-2">
        <span className="text-[10px] text-text-muted font-mono uppercase tracking-wider">Avg/Trade</span>
        <span className={`text-xs font-mono font-medium ${pnlColor(avgPnl)}`}>
          {formatINR(avgPnl)}
        </span>
      </div>

      <div className="w-px h-4 bg-border" />

      <div className="flex items-center gap-2">
        <span className="text-[10px] text-text-muted font-mono uppercase tracking-wider">Profit Factor</span>
        <span className={`text-xs font-mono font-medium ${pnlColor(grossProfit - grossLoss)}`}>
          {profitFactor}
        </span>
      </div>

      {peakMargin != null && peakMargin > 0 && (
        <>
          <div className="w-px h-4 bg-border" />
          <div className="flex items-center gap-2">
            <span className="text-[10px] text-text-muted font-mono uppercase tracking-wider">Peak Margin</span>
            <span className="text-xs font-mono font-medium text-accent">
              {formatINR(peakMargin)}
            </span>
          </div>
        </>
      )}

      {closed.length === 0 && trades.length === 0 && (
        <span className="text-xs font-mono text-text-muted ml-auto">no trades in this period</span>
      )}
    </div>
  );
}

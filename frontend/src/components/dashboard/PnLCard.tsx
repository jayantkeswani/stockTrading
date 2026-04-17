"use client";

import { useMemo } from "react";
import { useStore } from "@/store";
import { formatINR, formatPercent, pnlColor } from "@/lib/formatters";

export function PnLCard() {
  const { risk, positions, prices } = useStore();

  // Compute live unrealized P&L from positions + real-time prices
  const liveUnrealizedPnl = useMemo(() => {
    return positions.reduce((total, pos) => {
      const priceKey = pos.fyers_option_symbol || pos.symbol;
      const livePrice = prices[priceKey]?.ltp;
      const currentPrice = livePrice ?? pos.current_price;
      if (currentPrice && pos.entry_price > 0) {
        return total + (currentPrice - pos.entry_price) * pos.quantity;
      }
      return total + (pos.unrealized_pnl ?? 0);
    }, 0);
  }, [positions, prices]);

  // Total P&L = closed trades P&L (from backend) + live unrealized P&L
  const closedPnl = Number(risk?.closed_pnl ?? 0);
  const pnl = closedPnl + liveUnrealizedPnl;

  const capital = Number(risk?.capital ?? 1000000);
  const drawdown = capital > 0 ? Math.abs(Math.min(pnl, 0)) / capital * 100 : 0;

  // Capital at risk: sum of entry_price * quantity for open positions
  const capitalAtRisk = useMemo(() => {
    return positions.reduce((total, pos) => {
      return total + pos.entry_price * pos.quantity;
    }, 0);
  }, [positions]);

  return (
    <div className="flex items-center gap-6 px-3 py-1.5 rounded border border-border bg-bg-secondary">
      {/* Today's P&L */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted font-mono uppercase">P&L</span>
        <span className={`text-sm font-bold font-mono ${pnlColor(pnl)}`}>
          {formatINR(pnl)}
        </span>
      </div>

      <div className="w-px h-4 bg-border" />

      {/* Drawdown */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted font-mono uppercase">DD</span>
        <span className={`text-xs font-mono font-medium ${drawdown > 3 ? "text-loss" : "text-text-secondary"}`}>
          {formatPercent(-drawdown)}
        </span>
        <div className="w-16 h-1 bg-bg-tertiary rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all ${
              drawdown > 4 ? "bg-loss" : drawdown > 2 ? "bg-warning" : "bg-accent"
            }`}
            style={{ width: `${Math.min(drawdown / 5 * 100, 100)}%` }}
          />
        </div>
      </div>

      <div className="w-px h-4 bg-border" />

      {/* Trades count */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted font-mono uppercase">TRADES</span>
        <span className="text-xs font-mono text-text-primary">
          {risk?.trades_today ?? 0}/{risk?.max_trades_per_day ?? 3}
        </span>
        <span className="text-xs text-text-muted font-mono">
          {positions.length} open
        </span>
      </div>

      <div className="w-px h-4 bg-border" />

      {/* Capital at risk */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted font-mono uppercase">RISK</span>
        <span className="text-xs font-mono text-text-primary">
          {formatINR(capitalAtRisk)}
        </span>
      </div>

      {risk?.is_halted && (
        <>
          <div className="w-px h-4 bg-border" />
          <span className="text-xs font-mono font-bold text-loss glow-loss px-1.5 py-0.5 rounded bg-loss/10">
            HALTED
          </span>
        </>
      )}
    </div>
  );
}

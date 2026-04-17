"use client";

import { useStore } from "@/store";
import { formatINR, formatPercent, pnlColor } from "@/lib/formatters";

export function PnLCard() {
  const { risk, positions } = useStore();
  const pnl = risk?.daily_pnl ?? 0;
  const drawdown = risk?.daily_drawdown_pct ?? 0;

  return (
    <div className="flex items-center gap-6 px-3 py-1.5 rounded border border-border bg-bg-secondary">
      {/* Today's P&L */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-text-muted font-mono uppercase">P&L</span>
        <span className={`text-sm font-bold font-mono ${pnlColor(pnl)}`}>
          {formatINR(pnl)}
        </span>
      </div>

      <div className="w-px h-4 bg-border" />

      {/* Drawdown */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-text-muted font-mono uppercase">DD</span>
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
        <span className="text-[10px] text-text-muted font-mono uppercase">TRADES</span>
        <span className="text-xs font-mono text-text-primary">
          {risk?.trades_today ?? 0}/{risk?.max_trades_per_day ?? 3}
        </span>
        <span className="text-[10px] text-text-muted font-mono">
          {positions.length} open
        </span>
      </div>

      <div className="w-px h-4 bg-border" />

      {/* Capital at risk */}
      <div className="flex items-center gap-2">
        <span className="text-[10px] text-text-muted font-mono uppercase">RISK</span>
        <span className="text-xs font-mono text-text-primary">
          {formatINR(risk?.capital_at_risk ?? 0)}
        </span>
      </div>

      {risk?.is_halted && (
        <>
          <div className="w-px h-4 bg-border" />
          <span className="text-[10px] font-mono font-bold text-loss glow-loss px-1.5 py-0.5 rounded bg-loss/10">
            HALTED
          </span>
        </>
      )}
    </div>
  );
}

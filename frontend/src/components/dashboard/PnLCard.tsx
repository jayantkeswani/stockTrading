"use client";

import { useStore } from "@/store";
import { formatINR, formatPercent, pnlColor } from "@/lib/formatters";

export function PnLCard() {
  const { risk, positions } = useStore();
  const pnl = risk?.daily_pnl ?? 0;
  const drawdown = risk?.daily_drawdown_pct ?? 0;

  return (
    <div className="rounded-lg border border-border bg-bg-secondary p-4">
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Today's P&L */}
        <div>
          <div className="text-xs text-text-muted uppercase tracking-wider mb-1">
            Today&apos;s P&amp;L
          </div>
          <div className={`text-xl font-bold font-mono ${pnlColor(pnl)}`}>
            {formatINR(pnl)}
          </div>
        </div>

        {/* Drawdown meter */}
        <div>
          <div className="text-xs text-text-muted uppercase tracking-wider mb-1">
            Drawdown
          </div>
          <div className="flex items-center gap-2">
            <span className={`text-sm font-mono font-semibold ${drawdown > 3 ? "text-loss" : "text-text-secondary"}`}>
              {formatPercent(-drawdown)}
            </span>
          </div>
          <div className="mt-1 flex items-center gap-1.5">
            <div className="flex-1 h-1.5 bg-bg-tertiary rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  drawdown > 4 ? "bg-loss" : drawdown > 2 ? "bg-warning" : "bg-accent"
                }`}
                style={{ width: `${Math.min(drawdown / 5 * 100, 100)}%` }}
              />
            </div>
            <span className="text-[10px] text-text-muted">5%</span>
          </div>
        </div>

        {/* Trades count */}
        <div>
          <div className="text-xs text-text-muted uppercase tracking-wider mb-1">
            Trades
          </div>
          <div className="text-sm font-mono text-text-primary">
            {risk?.trades_today ?? 0} / {risk?.max_trades_per_day ?? 3}
          </div>
          <div className="text-xs text-text-muted mt-0.5">
            {positions.length} open
          </div>
        </div>

        {/* Capital at risk */}
        <div>
          <div className="text-xs text-text-muted uppercase tracking-wider mb-1">
            Capital Risk
          </div>
          <div className="text-sm font-mono text-text-primary">
            {formatINR(risk?.capital_at_risk ?? 0)}
          </div>
        </div>
      </div>

      {risk?.is_halted && (
        <div className="mt-3 px-3 py-1.5 rounded bg-loss/20 text-loss text-xs text-center font-semibold">
          TRADING HALTED - Drawdown Limit
        </div>
      )}
    </div>
  );
}

"use client";

import { useMemo } from "react";
import { useStore } from "@/store";
import { formatINR, formatPercent, pnlColor } from "@/lib/formatters";

export function PnLCard() {
  const { risk, positions, prices, dashboardViewMode, shadowPositions, shadowClosedToday, positionsMinConfidence } = useStore();

  const isShadow = dashboardViewMode === "SHADOW";
  const rawPositions = isShadow ? shadowPositions : positions;
  const activePositions = positionsMinConfidence > 0
    ? rawPositions.filter((p) => p.signal_confidence != null && Number(p.signal_confidence) >= positionsMinConfidence)
    : rawPositions;

  // Compute live unrealized P&L from active positions + real-time prices
  const liveUnrealizedPnl = useMemo(() => {
    return activePositions.reduce((total, pos) => {
      const priceKey = pos.fyers_option_symbol || pos.symbol;
      const livePrice = prices[priceKey]?.ltp;
      const currentPrice = livePrice ?? pos.current_price;
      if (currentPrice && pos.entry_price > 0) {
        const isShort = pos.target_price != null && pos.target_price < pos.entry_price;
        const diff = isShort ? pos.entry_price - currentPrice : currentPrice - pos.entry_price;
        return total + diff * pos.quantity;
      }
      return total + (pos.unrealized_pnl ?? 0);
    }, 0);
  }, [activePositions, prices]);

  // In shadow mode: closed P&L from shadow trades today (computed client-side)
  const filteredShadowClosed = positionsMinConfidence > 0
    ? shadowClosedToday.filter((t) => t.signal_confidence != null && Number(t.signal_confidence) >= positionsMinConfidence)
    : shadowClosedToday;
  const shadowClosedPnl = useMemo(() => {
    return filteredShadowClosed.reduce((sum, t) => sum + Number(t.pnl ?? 0), 0);
  }, [filteredShadowClosed]);

  const closedPnl = isShadow ? shadowClosedPnl : Number(risk?.closed_pnl ?? 0);
  const pnl = closedPnl + liveUnrealizedPnl;

  const capital = Number(risk?.capital ?? 1000000);
  const drawdown = capital > 0 ? Math.abs(Math.min(pnl, 0)) / capital * 100 : 0;

  const notional = useMemo(() => {
    return activePositions.reduce((total, pos) => total + pos.entry_price * pos.quantity, 0);
  }, [activePositions]);

  const riskTotal = useMemo(() => {
    return activePositions.reduce((total, pos) =>
      total + Math.abs(pos.entry_price - pos.stop_loss) * pos.quantity, 0);
  }, [activePositions]);

  const marginTotal = useMemo(() => {
    return activePositions.reduce((total, pos) => total + Number(pos.margin_required ?? 0), 0);
  }, [activePositions]);

  const tradesCount = isShadow
    ? filteredShadowClosed.length
    : (risk?.trades_today ?? 0);

  return (
    <div className={`flex items-center gap-6 px-3 py-1.5 rounded border bg-bg-secondary ${
      isShadow ? "border-purple-500/30" : "border-border"
    }`}>
      {isShadow && (
        <span className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-400 shrink-0">
          SHADOW
        </span>
      )}

      {/* Day P&L — total with inline breakdown */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted font-mono uppercase">Day P&L</span>
        <span className={`text-sm font-bold font-mono ${pnlColor(pnl)}`}>
          {formatINR(pnl)}
        </span>
        <span className="text-[10px] font-mono text-text-muted">
          (U: <span className={pnlColor(liveUnrealizedPnl)}>{formatINR(liveUnrealizedPnl)}</span>
          {" + R: "}<span className={pnlColor(closedPnl)}>{formatINR(closedPnl)}</span>)
        </span>
      </div>

      {!isShadow && (
        <>
          <div className="w-px h-4 bg-border" />

          {/* Drawdown — only meaningful for real trades */}
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
        </>
      )}

      <div className="w-px h-4 bg-border" />

      {/* Trades / positions count */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted font-mono uppercase">
          {isShadow ? "CLOSED" : "TRADES"}
        </span>
        <span className="text-xs font-mono text-text-primary">
          {isShadow ? tradesCount : `${tradesCount}/${risk?.max_trades_per_day ?? 3}`}
        </span>
        <span className="text-xs text-text-muted font-mono">
          {activePositions.length} open
        </span>
      </div>

      <div className="w-px h-4 bg-border" />

      {/* Risk metrics */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted font-mono uppercase">NOTIONAL</span>
        <span className="text-xs font-mono text-text-primary">{formatINR(notional)}</span>
      </div>
      <div className="w-px h-4 bg-border" />
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted font-mono uppercase">RISK</span>
        <span className="text-xs font-mono text-text-primary">{formatINR(riskTotal)}</span>
      </div>
      <div className="w-px h-4 bg-border" />
      <div className="flex items-center gap-2">
        <span className="text-xs text-text-muted font-mono uppercase">MARGIN</span>
        <span className="text-xs font-mono text-text-primary">{formatINR(marginTotal)}</span>
      </div>

      {!isShadow && risk?.is_halted && (
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

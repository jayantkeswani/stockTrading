"use client";

import { Fragment, useState, useEffect } from "react";
import { useStore } from "@/store";
import { formatINR, formatPercent, pnlColor } from "@/lib/formatters";
import { STRATEGY_LABELS } from "@/lib/constants";
import { api } from "@/lib/api";
import { subscribeSymbols } from "@/hooks/useWebSocket";

interface ActivePositionsProps {
  compact?: boolean;
}

export function ActivePositions({ compact }: ActivePositionsProps) {
  const { positions, prices } = useStore();
  const [expandedId, setExpandedId] = useState<string | null>(null);

  // Subscribe position price symbols on the frontend WebSocket for live ticks
  useEffect(() => {
    const symbols = positions
      .map((p) => p.fyers_option_symbol || p.symbol)
      .filter(Boolean);
    if (symbols.length > 0) {
      subscribeSymbols(symbols);
    }
  }, [positions]);

  const handleClose = async (positionId: string) => {
    if (!confirm("Close this position?")) return;
    try {
      await api.closePosition(positionId);
    } catch (err) {
      console.error("Failed to close position:", err);
    }
  };

  const toggleExpand = (id: string) => {
    setExpandedId((prev) => (prev === id ? null : id));
  };

  return (
    <div className="rounded border border-border bg-bg-secondary">
      <div className="px-3 py-1.5 border-b border-border flex items-center gap-2">
        <h2 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          Positions
        </h2>
        {positions.length > 0 && (
          <span className="text-xs font-mono text-text-muted">({positions.length})</span>
        )}
      </div>

      {positions.length === 0 ? (
        <div className="px-3 py-4 text-center text-text-muted text-xs font-mono">
          no open positions
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead>
              <tr className="text-[10px] text-text-muted uppercase font-mono tracking-wider">
                <th className="text-left px-3 py-1">Symbol</th>
                {!compact && <th className="text-left px-3 py-1">Strike</th>}
                <th className="text-right px-3 py-1">Entry</th>
                <th className="text-right px-3 py-1">LTP</th>
                <th className="text-right px-3 py-1">P&L</th>
                <th className="text-right px-3 py-1">SL Dist</th>
                {!compact && <th className="text-left px-3 py-1">Strategy</th>}
                <th className="text-right px-3 py-1"></th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => {
                // Use live price from store if available, else fall back to DB value
                const priceKey = pos.fyers_option_symbol || pos.symbol;
                const livePrice = prices[priceKey]?.ltp;
                const currentPrice = livePrice ?? pos.current_price;

                const pnl = currentPrice && pos.entry_price > 0
                  ? (currentPrice - pos.entry_price) * pos.quantity
                  : (pos.unrealized_pnl ?? 0);
                const pnlPct =
                  pos.entry_price > 0 && currentPrice
                    ? ((currentPrice - pos.entry_price) / pos.entry_price) * 100
                    : 0;
                const slDistance =
                  currentPrice && pos.stop_loss
                    ? ((currentPrice - pos.stop_loss) / currentPrice) * 100
                    : 0;
                const isExpanded = expandedId === pos.id;

                return (
                  <Fragment key={pos.id}>
                    <tr
                      className="border-t border-border/30 hover:bg-bg-tertiary/40 transition-colors cursor-pointer"
                      onClick={() => toggleExpand(pos.id)}
                    >
                      <td className="px-3 py-1.5">
                        <span className="font-mono font-medium">{pos.symbol}</span>
                        <span
                          className={`ml-1 text-[10px] font-mono ${
                            pos.option_type === "CE" ? "text-profit" : "text-loss"
                          }`}
                        >
                          {pos.option_type}
                        </span>
                        {compact && (
                          <span className="ml-1 text-[10px] text-text-muted font-mono">
                            {pos.strike_price}
                          </span>
                        )}
                      </td>
                      {!compact && (
                        <td className="px-3 py-1.5 font-mono text-text-secondary">
                          {pos.strike_price}
                        </td>
                      )}
                      <td className="px-3 py-1.5 text-right font-mono">
                        {formatINR(pos.entry_price)}
                      </td>
                      <td className="px-3 py-1.5 text-right font-mono">
                        {currentPrice ? formatINR(currentPrice) : "\u2014"}
                      </td>
                      <td className={`px-3 py-1.5 text-right font-mono font-medium ${pnlColor(pnl)}`}>
                        <div>{formatINR(pnl)}</div>
                        <div className="text-[10px]">{formatPercent(pnlPct)}</div>
                      </td>
                      <td className="px-3 py-1.5 text-right">
                        <span
                          className={`text-xs font-mono ${
                            slDistance < 1 ? "text-loss font-bold" : slDistance < 3 ? "text-warning" : "text-text-muted"
                          }`}
                        >
                          {slDistance.toFixed(1)}%
                        </span>
                      </td>
                      {!compact && (
                        <td className="px-3 py-1.5">
                          <span className="text-[10px] font-mono px-1 py-px rounded bg-accent/10 text-accent">
                            {STRATEGY_LABELS[pos.strategy_name] || pos.strategy_name}
                          </span>
                        </td>
                      )}
                      <td className="px-3 py-1.5 text-right">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleClose(pos.id);
                          }}
                          className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-loss/10 text-loss hover:bg-loss/20 transition-colors"
                        >
                          CLOSE
                        </button>
                      </td>
                    </tr>
                    {isExpanded && (
                      <tr key={`${pos.id}-expanded`} className="bg-bg-tertiary/20">
                        <td colSpan={compact ? 6 : 8} className="px-3 py-2">
                          <div className="grid grid-cols-4 gap-3 text-xs font-mono animate-fade-in">
                            <div>
                              <span className="text-text-muted">Strategy</span>
                              <div className="text-text-primary mt-0.5">
                                {STRATEGY_LABELS[pos.strategy_name] || pos.strategy_name}
                              </div>
                            </div>
                            <div>
                              <span className="text-text-muted">Expiry</span>
                              <div className="text-text-primary mt-0.5">{pos.expiry_date}</div>
                            </div>
                            <div>
                              <span className="text-text-muted">Lots / Qty</span>
                              <div className="text-text-primary mt-0.5">
                                {pos.lots}L / {pos.quantity}
                              </div>
                            </div>
                            <div>
                              <span className="text-text-muted">Stop Loss</span>
                              <div className="text-loss mt-0.5">{formatINR(pos.stop_loss)}</div>
                            </div>
                            {pos.target_price && (
                              <div>
                                <span className="text-text-muted">Target</span>
                                <div className="text-profit mt-0.5">{formatINR(pos.target_price)}</div>
                              </div>
                            )}
                            <div>
                              <span className="text-text-muted">Type</span>
                              <div className="text-text-primary mt-0.5">
                                {pos.is_paper ? "PAPER" : "LIVE"} / {pos.position_type}
                              </div>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

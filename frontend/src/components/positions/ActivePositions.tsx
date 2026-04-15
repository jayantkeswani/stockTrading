"use client";

import { useState } from "react";
import { useStore } from "@/store";
import { formatINR, formatPercent, pnlColor } from "@/lib/formatters";
import { STRATEGY_LABELS } from "@/lib/constants";
import { api } from "@/lib/api";

interface ActivePositionsProps {
  compact?: boolean;
}

export function ActivePositions({ compact }: ActivePositionsProps) {
  const { positions } = useStore();
  const [expandedId, setExpandedId] = useState<string | null>(null);

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
    <div className="rounded-lg border border-border bg-bg-secondary">
      <div className="px-4 py-3 border-b border-border">
        <h2 className="text-sm font-semibold text-text-primary">
          Active Positions
          {positions.length > 0 && (
            <span className="ml-2 text-xs text-text-muted">({positions.length})</span>
          )}
        </h2>
      </div>

      {positions.length === 0 ? (
        <div className="p-6 text-center text-text-muted text-sm">
          No open positions
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-text-muted uppercase tracking-wider">
                <th className="text-left px-4 py-2">Symbol</th>
                {!compact && <th className="text-left px-4 py-2">Strike</th>}
                <th className="text-right px-4 py-2">Entry</th>
                <th className="text-right px-4 py-2">LTP</th>
                <th className="text-right px-4 py-2">P&L</th>
                <th className="text-right px-4 py-2">SL Dist</th>
                {!compact && <th className="text-left px-4 py-2">Strategy</th>}
                <th className="text-right px-4 py-2">Action</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => {
                const pnl = pos.unrealized_pnl ?? 0;
                const pnlPct =
                  pos.entry_price > 0 && pos.current_price
                    ? ((pos.current_price - pos.entry_price) / pos.entry_price) * 100
                    : 0;
                const slDistance =
                  pos.current_price && pos.stop_loss
                    ? ((pos.current_price - pos.stop_loss) / pos.current_price) * 100
                    : 0;
                const isExpanded = expandedId === pos.id;

                return (
                  <>
                    <tr
                      key={pos.id}
                      className="border-t border-border/50 hover:bg-bg-tertiary/50 transition-colors cursor-pointer"
                      onClick={() => toggleExpand(pos.id)}
                    >
                      <td className="px-4 py-2.5">
                        <span className="font-medium">{pos.symbol}</span>
                        <span
                          className={`ml-1.5 text-xs px-1 py-0.5 rounded ${
                            pos.option_type === "CE"
                              ? "bg-profit/20 text-profit"
                              : "bg-loss/20 text-loss"
                          }`}
                        >
                          {pos.option_type}
                        </span>
                        {compact && (
                          <span className="ml-1 text-xs text-text-muted font-mono">
                            {pos.strike_price}
                          </span>
                        )}
                      </td>
                      {!compact && (
                        <td className="px-4 py-2.5 font-mono text-text-secondary">
                          {pos.strike_price}
                        </td>
                      )}
                      <td className="px-4 py-2.5 text-right font-mono">
                        {formatINR(pos.entry_price)}
                      </td>
                      <td className="px-4 py-2.5 text-right font-mono">
                        {pos.current_price ? formatINR(pos.current_price) : "\u2014"}
                      </td>
                      <td className={`px-4 py-2.5 text-right font-mono font-semibold ${pnlColor(pnl)}`}>
                        <div>{formatINR(pnl)}</div>
                        <div className="text-xs">{formatPercent(pnlPct)}</div>
                      </td>
                      <td className="px-4 py-2.5 text-right">
                        <span
                          className={`text-xs font-mono ${
                            slDistance < 1 ? "text-loss font-bold" : slDistance < 3 ? "text-warning" : "text-text-secondary"
                          }`}
                        >
                          {slDistance.toFixed(1)}%
                        </span>
                      </td>
                      {!compact && (
                        <td className="px-4 py-2.5">
                          <span className="text-xs px-1.5 py-0.5 rounded bg-accent/20 text-accent">
                            {STRATEGY_LABELS[pos.strategy_name] || pos.strategy_name}
                          </span>
                        </td>
                      )}
                      <td className="px-4 py-2.5 text-right">
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleClose(pos.id);
                          }}
                          className="text-xs px-2 py-1 rounded bg-loss/20 text-loss hover:bg-loss/30 transition-colors"
                        >
                          Close
                        </button>
                      </td>
                    </tr>
                    {/* Expanded row */}
                    {isExpanded && (
                      <tr key={`${pos.id}-expanded`} className="bg-bg-tertiary/30">
                        <td colSpan={compact ? 6 : 8} className="px-4 py-3">
                          <div className="grid grid-cols-4 gap-4 text-xs">
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
                              <div className="text-loss mt-0.5 font-mono">{formatINR(pos.stop_loss)}</div>
                            </div>
                            {pos.target_price && (
                              <div>
                                <span className="text-text-muted">Target</span>
                                <div className="text-profit mt-0.5 font-mono">{formatINR(pos.target_price)}</div>
                              </div>
                            )}
                            <div>
                              <span className="text-text-muted">Type</span>
                              <div className="text-text-primary mt-0.5">
                                {pos.is_paper ? "Paper" : "Live"}
                              </div>
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

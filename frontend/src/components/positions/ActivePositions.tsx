"use client";

import { Fragment, useState, useEffect, useRef, useCallback } from "react";
import { useStore } from "@/store";
import { formatINR, formatPercent, pnlColor } from "@/lib/formatters";
import { STRATEGY_LABELS } from "@/lib/constants";
import { api } from "@/lib/api";
import { subscribeSymbols } from "@/hooks/useWebSocket";
import type { Position } from "@/lib/types";

interface ActivePositionsProps {
  compact?: boolean;
}

interface PositionRowsProps {
  list: Position[];
  prices: Record<string, { ltp?: number }>;
  expandedId: string | null;
  toggleExpand: (id: string) => void;
  handleClose: (id: string) => void;
  isShadow: boolean;
  compact?: boolean;
}

function formatISTTime(isoString: string | null): string {
  if (!isoString) return "—";
  const d = new Date(isoString);
  return d.toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
}

export function ActivePositions({ compact }: ActivePositionsProps) {
  const {
    positions, prices, closedToday, setClosedToday,
    positionViewMode, setPositionViewMode,
    shadowPositions, setShadowPositions,
    shadowClosedToday, setShadowClosedToday,
  } = useStore();

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const prevPositionsLen = useRef(positions.length);

  const isShadow = positionViewMode === "SHADOW";

  // Fetch real closed trades today
  const fetchClosed = useCallback(async () => {
    try {
      const trades = await api.getClosedTradesToday();
      setClosedToday(trades);
    } catch { /* non-critical */ }
  }, [setClosedToday]);

  // Fetch shadow data (positions + closed today)
  const fetchShadowData = useCallback(async () => {
    try {
      const [allPositions, closedTrades] = await Promise.all([
        api.getPositions(true) as Promise<Position[]>,
        api.getClosedTradesToday("SHADOW"),
      ]);
      setShadowPositions((allPositions as Position[]).filter((p) => p.is_shadow));
      setShadowClosedToday(closedTrades);
    } catch { /* non-critical */ }
  }, [setShadowPositions, setShadowClosedToday]);

  useEffect(() => { fetchClosed(); }, [fetchClosed]);

  // Re-fetch closed trades when a real position is removed
  useEffect(() => {
    if (positions.length < prevPositionsLen.current) fetchClosed();
    prevPositionsLen.current = positions.length;
  }, [positions.length, fetchClosed]);

  // Poll shadow data every 30s when in shadow mode
  useEffect(() => {
    if (!isShadow) return;
    fetchShadowData();
    const id = setInterval(fetchShadowData, 30_000);
    return () => clearInterval(id);
  }, [isShadow, fetchShadowData]);

  // Subscribe position price symbols for live ticks
  useEffect(() => {
    const active = isShadow ? shadowPositions : positions;
    const symbols = active.map((p) => p.fyers_option_symbol || p.symbol).filter(Boolean);
    if (symbols.length > 0) subscribeSymbols(symbols);
  }, [positions, shadowPositions, isShadow]);

  const handleClose = async (positionId: string) => {
    if (!confirm("Close this position?")) return;
    try {
      await api.closePosition(positionId);
      useStore.getState().removePosition(positionId);
      setTimeout(fetchClosed, 300);
    } catch (err) {
      console.error("Failed to close position:", err);
    }
  };

  const handleToggle = () => {
    const next = isShadow ? "REAL" : "SHADOW";
    setPositionViewMode(next);
  };

  const toggleExpand = (id: string) => setExpandedId((prev) => (prev === id ? null : id));

  const activePositions = isShadow ? shadowPositions : positions;
  const activeClosed = isShadow ? shadowClosedToday : closedToday;

  const rowProps: PositionRowsProps = {
    list: activePositions, prices, expandedId, toggleExpand, handleClose, isShadow, compact,
  };

  return (
    <div className={`rounded border bg-bg-secondary ${isShadow ? "border-purple-500/30" : "border-border"}`}>
      <div className="px-3 py-1.5 border-b border-border flex items-center gap-2">
        <h2 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          Positions
        </h2>
        {activePositions.length > 0 && (
          <span className="text-xs font-mono text-text-muted">({activePositions.length})</span>
        )}
        <div className="ml-auto flex items-center rounded border border-border overflow-hidden text-[9px] font-mono">
          <button
            onClick={handleToggle}
            className={`px-1.5 py-0.5 transition-colors ${!isShadow ? "bg-accent/15 text-accent" : "text-text-muted hover:text-text-secondary"}`}
          >
            Real
          </button>
          <button
            onClick={handleToggle}
            className={`px-1.5 py-0.5 border-l border-border transition-colors ${isShadow ? "bg-purple-500/15 text-purple-400" : "text-text-muted hover:text-text-secondary"}`}
          >
            Ghost
          </button>
        </div>
      </div>

      {isShadow && (
        <div className="px-3 py-1 border-b border-purple-500/20 bg-purple-500/5">
          <span className="text-[9px] font-mono text-purple-400/70">Signal Test — ghost trades auto-close at SL/target/EOD</span>
        </div>
      )}

      {activePositions.length === 0 && activeClosed.length === 0 ? (
        <div className="px-3 py-4 text-center text-text-muted text-xs font-mono">
          {isShadow ? "no ghost positions yet" : "no open positions"}
        </div>
      ) : activePositions.length === 0 ? null : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs" style={{ tableLayout: "fixed" }}>
            <colgroup>
              <col />
              {!compact && <col style={{ width: "80px" }} />}
              <col style={{ width: "100px" }} />
              <col style={{ width: "100px" }} />
              <col style={{ width: "120px" }} />
              <col style={{ width: "70px" }} />
              {!compact && <col style={{ width: "100px" }} />}
              <col style={{ width: "60px" }} />
            </colgroup>
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
              <PositionRows {...rowProps} />
            </tbody>
          </table>
        </div>
      )}

      {/* Closed Today (real or ghost) */}
      {activeClosed.length > 0 && (
        <div className="border-t border-border/50">
          <div className="px-3 py-1.5 border-b border-border/30 flex items-center gap-2">
            <span className="text-[10px] font-mono text-text-muted uppercase tracking-wider">
              {isShadow ? "Ghost Closed Today" : "Closed Today"}
            </span>
            <span className="text-[10px] font-mono text-text-muted">({activeClosed.length})</span>
          </div>
          <div className="divide-y divide-border/20">
            {activeClosed.map((t) => {
              const durationMin = t.entry_time && t.exit_time
                ? Math.round((new Date(t.exit_time).getTime() - new Date(t.entry_time).getTime()) / 60000)
                : null;
              const isFutures = !t.option_type;
              return (
                <div key={t.id} className="px-3 py-2 opacity-60 hover:opacity-80 transition-opacity">
                  {/* Row 1: symbol + side + lots + P&L */}
                  <div className="flex items-center gap-2">
                    <span className="font-mono font-medium text-xs">{t.symbol}</span>
                    {!isFutures && (
                      <span className={`text-[10px] font-mono ${t.option_type === "CE" ? "text-profit" : "text-loss"}`}>
                        {t.option_type}
                      </span>
                    )}
                    {t.strike_price > 0 && (
                      <span className="text-[10px] font-mono text-text-muted">{t.strike_price}</span>
                    )}
                    <span className={`text-[9px] font-mono px-1 py-px rounded border ${
                      t.side === "BUY"
                        ? "border-profit/30 text-profit"
                        : "border-loss/30 text-loss"
                    }`}>
                      {t.side}
                    </span>
                    <span className="text-[10px] font-mono text-text-muted">{t.lots}L</span>
                    <span className="text-[10px] font-mono px-1 py-px rounded bg-accent/10 text-accent ml-auto">
                      {STRATEGY_LABELS[t.strategy_name] || t.strategy_name}
                    </span>
                  </div>
                  {/* Row 2: entry→exit prices + P&L + times + duration */}
                  <div className="flex items-center gap-3 mt-0.5">
                    <span className="text-[10px] font-mono text-text-muted">
                      {formatINR(t.entry_price)}
                      <span className="mx-1 text-text-muted/40">→</span>
                      {t.exit_price ? formatINR(t.exit_price) : "—"}
                    </span>
                    <span className={`text-[10px] font-mono font-medium ${pnlColor(t.pnl ?? 0)}`}>
                      {formatINR(t.pnl ?? 0)}
                      {t.pnl_percent != null && (
                        <span className="ml-1 opacity-70">{formatPercent(t.pnl_percent)}</span>
                      )}
                    </span>
                    <span className="text-[9px] font-mono text-text-muted/60 ml-auto">
                      {formatISTTime(t.entry_time)}
                      <span className="mx-1">–</span>
                      {formatISTTime(t.exit_time)}
                      {durationMin != null && (
                        <span className="ml-1.5 text-text-muted/40">{durationMin}m</span>
                      )}
                    </span>
                    <span className="text-[9px] font-mono text-text-muted/50">
                      {t.exit_reason?.replace(/_/g, " ") ?? "—"}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function PositionRows({ list, prices, expandedId, toggleExpand, handleClose, isShadow, compact }: PositionRowsProps) {
  return (
    <>
      {list.map((pos) => {
        const priceKey = pos.fyers_option_symbol || pos.symbol;
        const livePrice = prices[priceKey]?.ltp;
        const currentPrice = livePrice ?? pos.current_price;
        const isShort = pos.target_price != null && pos.target_price < pos.entry_price;
        const isFutures = !pos.option_type;
        const priceDiff = isShort ? pos.entry_price - currentPrice : currentPrice - pos.entry_price;
        const pnl = currentPrice && pos.entry_price > 0
          ? priceDiff * pos.quantity
          : (pos.unrealized_pnl ?? 0);
        const pnlPct = pos.entry_price > 0 && currentPrice
          ? (priceDiff / pos.entry_price) * 100
          : 0;
        const slDistance = currentPrice && pos.stop_loss
          ? isShort
            ? ((pos.stop_loss - currentPrice) / currentPrice) * 100
            : ((currentPrice - pos.stop_loss) / currentPrice) * 100
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
                {isFutures ? (
                  <span className={`ml-1 text-[10px] font-mono ${isShort ? "text-loss" : "text-profit"}`}>
                    {isShort ? "SHORT" : "LONG"}
                  </span>
                ) : (
                  <span className={`ml-1 text-[10px] font-mono ${pos.option_type === "CE" ? "text-profit" : "text-loss"}`}>
                    {pos.option_type}
                  </span>
                )}
                {compact && !isFutures && (
                  <span className="ml-1 text-[10px] text-text-muted font-mono">{pos.strike_price}</span>
                )}
              </td>
              {!compact && (
                <td className="px-3 py-1.5 font-mono text-text-secondary">{isFutures ? "—" : pos.strike_price}</td>
              )}
              <td className="px-3 py-1.5 text-right font-mono">{formatINR(pos.entry_price)}</td>
              <td className="px-3 py-1.5 text-right font-mono">
                {currentPrice ? formatINR(currentPrice) : "—"}
              </td>
              <td className={`px-3 py-1.5 text-right font-mono font-medium ${pnlColor(pnl)}`}>
                <div>{formatINR(pnl)}</div>
                <div className="text-[10px]">{formatPercent(pnlPct)}</div>
              </td>
              <td className="px-3 py-1.5 text-right">
                <span className={`text-xs font-mono ${slDistance < 1 ? "text-loss font-bold" : slDistance < 3 ? "text-warning" : "text-text-muted"}`}>
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
                {!isShadow && (
                  <button
                    onClick={(e) => { e.stopPropagation(); handleClose(pos.id); }}
                    className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-loss/10 text-loss hover:bg-loss/20 transition-colors"
                  >
                    CLOSE
                  </button>
                )}
              </td>
            </tr>
            {isExpanded && (
              <tr key={`${pos.id}-expanded`} className="bg-bg-tertiary/20">
                <td colSpan={compact ? 6 : 8} className="px-3 py-2">
                  <div className="grid grid-cols-4 gap-3 text-xs font-mono animate-fade-in">
                    <div>
                      <span className="text-text-muted">Strategy</span>
                      <div className="text-text-primary mt-0.5">{STRATEGY_LABELS[pos.strategy_name] || pos.strategy_name}</div>
                    </div>
                    <div>
                      <span className="text-text-muted">Expiry</span>
                      <div className="text-text-primary mt-0.5">{pos.expiry_date}</div>
                    </div>
                    <div>
                      <span className="text-text-muted">Lots / Qty</span>
                      <div className="text-text-primary mt-0.5">{pos.lots}L / {pos.quantity}</div>
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
                        {isShadow ? "GHOST" : pos.is_paper ? "PAPER" : "LIVE"} / {pos.position_type}
                        {isFutures && (
                          <span className={`ml-1 ${isShort ? "text-loss" : "text-profit"}`}>
                            {isShort ? "SHORT" : "LONG"}
                          </span>
                        )}
                      </div>
                    </div>
                  </div>
                </td>
              </tr>
            )}
          </Fragment>
        );
      })}
    </>
  );
}

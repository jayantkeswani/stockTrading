"use client";

import { useEffect, useState, useCallback, useMemo, useRef } from "react";
import { useStore } from "@/store";
import { useShallow } from "zustand/react/shallow";
import { formatINR, formatPercent, pnlColor } from "@/lib/formatters";
import { STRATEGY_LABELS } from "@/lib/constants";
import { api } from "@/lib/api";
import { livePositionPnl, positionPriceKeys } from "@/lib/positionPnl";
import { usePrices } from "@/hooks/usePrices";
import { addToPersonalWatchlist } from "@/lib/watchlistAdd";
import type { Position, Trade } from "@/lib/types";

function formatISTTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
}

function formatLatency(fillTime: string, generatedAt: string | null): string | null {
  if (!generatedAt) return null;
  const diffMs = new Date(fillTime).getTime() - new Date(generatedAt).getTime();
  if (diffMs < 0 || isNaN(diffMs)) return null;
  const secs = Math.round(diffMs / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60), rem = secs % 60;
  if (mins < 60) return rem > 0 ? `${mins}m ${rem}s` : `${mins}m`;
  const hrs = Math.floor(mins / 60), remM = mins % 60;
  return remM > 0 ? `${hrs}h ${remM}m` : `${hrs}h`;
}

function KV({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[9px] font-mono text-text-muted tracking-wide">{label}</span>
      <span className={`text-[12px] font-mono font-medium mt-0.5 ${cls ?? ""}`}>{value}</span>
    </div>
  );
}

function GlanceCell({ k, v, cls }: { k: string; v: string; cls?: string }) {
  return (
    <div className="flex-1 text-center px-1 py-1.5 border-r border-border last:border-r-0">
      <div className="text-[9px] font-mono text-text-muted tracking-wide">{k}</div>
      <div className={`text-[13px] font-mono font-medium mt-0.5 ${cls ?? ""}`}>{v}</div>
    </div>
  );
}

function OpenPositionCard({ pos, prices, isShadow, onClose }: {
  pos: Position;
  prices: Record<string, { ltp?: number }>;
  isShadow: boolean;
  onClose: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [watchStatus, setWatchStatus] = useState<"idle" | "adding" | "done" | "error">("idle");
  const isFutures = !pos.option_type;
  const { currentPrice, pnl, pnlPct, slDistance: slDist, isShort } = livePositionPnl(pos, prices);
  const lat = formatLatency(pos.opened_at, pos.signal_generated_at);

  const handleWatch = async () => {
    if (watchStatus !== "idle") return;
    setWatchStatus("adding");
    const ok = await addToPersonalWatchlist({
      fyersSymbol: pos.fyers_option_symbol || null,
      symbol: pos.symbol,
      optionType: pos.option_type || null,
      strikePrice: pos.strike_price,
      expiryDate: pos.expiry_date ?? null,
    }).catch(() => false);
    setWatchStatus(ok ? "done" : "error");
    setTimeout(() => setWatchStatus("idle"), ok ? 2000 : 2500);
  };

  return (
    <div className="rounded-lg border border-border bg-bg-secondary mb-1.5 overflow-hidden">
      <button onClick={() => setOpen((o) => !o)} className="w-full flex items-center gap-2 px-3 py-2.5 text-left">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-[15px] font-mono font-bold text-text-primary truncate">{pos.symbol}</span>
            {pos.is_permanent_watchlist && (
              <span className="text-[9px] font-mono px-1 py-px rounded border border-accent/40 text-accent/70">P</span>
            )}
            <span className={`text-[11px] font-mono font-bold ${
              isFutures ? (isShort ? "text-loss" : "text-profit") : (pos.option_type === "CE" ? "text-profit" : "text-loss")
            }`}>
              {isFutures ? (isShort ? "▼" : "▲") : pos.option_type}
            </span>
          </div>
          <div className="text-[11px] font-mono text-text-muted mt-0.5 truncate">
            {pos.lots}L ({pos.quantity}) · {STRATEGY_LABELS[pos.strategy_name] || pos.strategy_name} · {formatISTTime(pos.opened_at)}
          </div>
        </div>
        <div className="text-right shrink-0">
          <div className={`text-[15px] font-mono font-bold ${pnlColor(pnl)}`}>{formatINR(pnl)}</div>
          <div className={`text-[10px] font-mono ${pnlColor(pnl)}`}>{formatPercent(pnlPct)}</div>
        </div>
        <span className={`text-text-muted text-[13px] transition-transform ${open ? "rotate-180" : ""}`}>▼</span>
      </button>

      <div className="flex border-t border-border">
        <GlanceCell k="ENTRY" v={formatINR(pos.entry_price)} />
        <GlanceCell k="LTP" v={currentPrice ? formatINR(currentPrice) : "—"} />
        <GlanceCell k="SL DIST" v={`${slDist.toFixed(1)}%`} cls={slDist < 1 ? "text-loss font-bold" : slDist < 3 ? "text-warning" : "text-text-muted"} />
        <GlanceCell k="TGT" v={pos.target_price ? formatINR(pos.target_price) : "—"} cls="text-profit" />
      </div>

      {open && (
        <div className="px-3 py-2.5 border-t border-border bg-bg-primary animate-fade-in">
          <div className="grid grid-cols-3 gap-y-2.5 gap-x-3">
            <KV label="Opened" value={formatISTTime(pos.opened_at)} />
            <KV label="Fill lat" value={lat ?? "—"} cls="text-accent" />
            <KV label="Strategy" value={STRATEGY_LABELS[pos.strategy_name] || pos.strategy_name} />
            <KV label="Expiry" value={pos.expiry_date || "—"} />
            <KV label="Lots / Qty" value={`${pos.lots}L (${pos.quantity})`} />
            <KV label="Stop loss" value={formatINR(pos.stop_loss)} cls="text-loss" />
            <KV label="Target" value={pos.target_price ? formatINR(pos.target_price) : "—"} cls="text-profit" />
            <KV label="Margin" value={pos.margin_required ? formatINR(Number(pos.margin_required)) : "—"} />
            <KV label="Conf" value={pos.signal_confidence != null ? String(pos.signal_confidence) : "—"} cls="text-accent" />
          </div>
          <div className="flex gap-2 mt-3">
            <button
              onClick={handleWatch}
              className={`flex-1 text-[12px] font-mono py-1.5 rounded border ${
                watchStatus === "done" ? "border-profit/30 text-profit bg-profit/10"
                  : watchStatus === "error" ? "border-loss/30 text-loss bg-loss/10"
                  : "border-border bg-bg-tertiary text-text-secondary"
              }`}
            >
              {watchStatus === "adding" ? "Adding…" : watchStatus === "done" ? "✓ Added" : watchStatus === "error" ? "Failed" : "+ Watch"}
            </button>
            {!isShadow && (
              <button
                onClick={() => onClose(pos.id)}
                className="flex-1 text-[12px] font-mono py-1.5 rounded bg-loss/10 text-loss border border-loss/25"
              >
                Close Position
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function ClosedTradeCard({ t }: { t: Trade }) {
  const [open, setOpen] = useState(false);
  const [watchStatus, setWatchStatus] = useState<"idle" | "adding" | "done" | "error">("idle");
  const isFutures = !t.option_type;
  const long = t.side === "BUY";
  const lat = formatLatency(t.entry_time, t.signal_snapshot?.generated_at ?? null);

  const handleWatch = async () => {
    if (watchStatus !== "idle") return;
    setWatchStatus("adding");
    const ok = await addToPersonalWatchlist({
      fyersSymbol: t.fyers_option_symbol ?? null,
      symbol: t.symbol,
      optionType: t.option_type || null,
      strikePrice: t.strike_price,
      expiryDate: t.expiry_date ?? null,
    }).catch(() => false);
    setWatchStatus(ok ? "done" : "error");
    setTimeout(() => setWatchStatus("idle"), ok ? 2000 : 2500);
  };
  return (
    <div className="rounded-lg border border-border bg-bg-secondary mb-1.5 overflow-hidden">
      <button onClick={() => setOpen((o) => !o)} className="w-full px-3 py-2.5 text-left">
        <div className="flex items-center gap-2">
          <span className={`text-[11px] font-mono font-bold ${long ? "text-profit" : "text-loss"}`}>{long ? "▲" : "▼"}</span>
          <span className="text-[14px] font-mono font-bold text-text-primary">{t.symbol}</span>
          {(t.is_permanent_watchlist || t.signal_is_permanent_watchlist) && (
            <span className="text-[9px] font-mono px-1 py-px rounded border border-accent/40 text-accent/70">P</span>
          )}
          {!isFutures && t.strike_price > 0 && (
            <span className="text-[11px] font-mono text-text-muted">{t.strike_price}{t.option_type}</span>
          )}
          <span className={`ml-auto text-[14px] font-mono font-bold ${pnlColor(t.pnl ?? 0)}`}>{formatINR(t.pnl ?? 0)}</span>
          <span className={`text-text-muted text-[13px] transition-transform ${open ? "rotate-180" : ""}`}>▼</span>
        </div>
        <div className="flex items-center gap-3 mt-1.5 text-[11px] font-mono text-text-muted">
          <span>{formatINR(t.entry_price)} → {t.exit_price ? formatINR(t.exit_price) : "—"}</span>
          <span>{t.lots}L</span>
          <span className={`ml-auto ${t.exit_reason === "TRAILING_SL" || t.exit_reason === "INVALIDATION" ? "text-warning" : ""}`}>
            {t.exit_reason?.replace(/_/g, " ") ?? "—"}
          </span>
        </div>
      </button>
      {open && (
        <div className="px-3 py-2.5 border-t border-border bg-bg-primary animate-fade-in">
          <div className="grid grid-cols-3 gap-y-2.5 gap-x-3">
            <KV label="Side" value={t.side} />
            <KV label="Lots / Qty" value={`${t.lots}L (${t.quantity})`} />
            <KV label="Net P&L" value={t.net_pnl != null ? formatINR(Number(t.net_pnl)) : "—"} cls={pnlColor(t.net_pnl ?? 0)} />
            <KV label="Entry → Exit" value={`${formatISTTime(t.entry_time)} – ${formatISTTime(t.exit_time)}`} />
            <KV label="Fill lat" value={lat ?? "—"} cls="text-accent" />
            <KV label="Margin" value={t.margin_required ? formatINR(Number(t.margin_required)) : "—"} />
            <KV label="Strategy" value={STRATEGY_LABELS[t.strategy_name] || t.strategy_name} />
            <KV label="Conf" value={t.signal_confidence != null ? String(t.signal_confidence) : "—"} cls="text-accent" />
            <KV label="P&L %" value={t.pnl_percent != null ? formatPercent(t.pnl_percent) : "—"} cls={pnlColor(t.pnl ?? 0)} />
          </div>
          <button
            onClick={handleWatch}
            className={`w-full mt-3 text-[12px] font-mono py-1.5 rounded border ${
              watchStatus === "done" ? "border-profit/30 text-profit bg-profit/10"
                : watchStatus === "error" ? "border-loss/30 text-loss bg-loss/10"
                : "border-border bg-bg-tertiary text-text-secondary"
            }`}
          >
            {watchStatus === "adding" ? "Adding…" : watchStatus === "done" ? "✓ Added" : watchStatus === "error" ? "Failed" : "+ Watch"}
          </button>
        </div>
      )}
    </div>
  );
}

/**
 * Mobile Positions tab: source selector bar (Manual / YOLO profiles / Shadow),
 * then open position cards (live P&L, expand for detail + Close) and a Closed
 * Today section. Mirrors ActivePositions' source-filtering and P&L logic.
 * Used by: MobileShell.
 */
export function MobilePositions({ refreshKey }: { refreshKey?: number }) {
  const {
    positions, closedToday, setClosedToday,
    dashboardViewMode, setDashboardViewMode,
    shadowPositions, setShadowPositions,
    shadowClosedToday, setShadowClosedToday,
    positionsMinConfidence, yoloProfiles,
  } = useStore(useShallow((s) => ({
    positions: s.positions,
    closedToday: s.closedToday, setClosedToday: s.setClosedToday,
    dashboardViewMode: s.dashboardViewMode, setDashboardViewMode: s.setDashboardViewMode,
    shadowPositions: s.shadowPositions, setShadowPositions: s.setShadowPositions,
    shadowClosedToday: s.shadowClosedToday, setShadowClosedToday: s.setShadowClosedToday,
    positionsMinConfidence: s.positionsMinConfidence,
    yoloProfiles: s.yoloProfiles,
  })));
  // Scoped to the open positions' price keys (real + shadow) — re-renders on
  // those ticks, not every symbol's.
  const priceKeys = useMemo(
    () => positionPriceKeys([...positions, ...shadowPositions]),
    [positions, shadowPositions],
  );
  const prices = usePrices(priceKeys);

  const prevLen = useRef(positions.length);

  const activeProfiles = useMemo(
    () => (Array.isArray(yoloProfiles) ? yoloProfiles : []).filter((p) => p.is_active).sort((a, b) => a.sort_order - b.sort_order),
    [yoloProfiles],
  );

  const effectiveMode = useMemo(() => {
    if (dashboardViewMode === "SHADOW") return "SHADOW";
    if (dashboardViewMode === "MANUAL") return "MANUAL";
    if ((Array.isArray(yoloProfiles) ? yoloProfiles : []).some((p) => p.id === dashboardViewMode)) return dashboardViewMode;
    return activeProfiles[0]?.id ?? "MANUAL";
  }, [dashboardViewMode, yoloProfiles, activeProfiles]);

  const isShadow = effectiveMode === "SHADOW";
  const isManual = effectiveMode === "MANUAL";

  const fetchClosed = useCallback(async () => {
    try { setClosedToday(await api.getClosedTradesToday()); } catch { /* non-critical */ }
  }, [setClosedToday]);

  const fetchShadow = useCallback(async () => {
    try {
      const [all, closed] = await Promise.all([
        api.getPositions({ includeShadow: true }) as Promise<Position[]>,
        api.getClosedTradesToday("SHADOW"),
      ]);
      setShadowPositions((all as Position[]).filter((p) => p.is_shadow));
      setShadowClosedToday(closed);
    } catch { /* non-critical */ }
  }, [setShadowPositions, setShadowClosedToday]);

  useEffect(() => { fetchClosed(); }, [fetchClosed, refreshKey]);

  useEffect(() => {
    if (positions.length < prevLen.current) fetchClosed();
    prevLen.current = positions.length;
  }, [positions.length, fetchClosed]);

  useEffect(() => {
    if (!isShadow) return;
    fetchShadow();
    const id = setInterval(fetchShadow, 30_000);
    return () => clearInterval(id);
  }, [isShadow, fetchShadow, refreshKey]);

  const handleClose = async (id: string) => {
    if (!confirm("Close this position?")) return;
    try {
      await api.closePosition(id);
      useStore.getState().removePosition(id);
      setTimeout(fetchClosed, 300);
    } catch (e) { console.error("Failed to close position:", e); }
  };

  const rawPositions = isShadow ? shadowPositions
    : isManual ? positions.filter((p) => !p.yolo_profile_id)
    : positions.filter((p) => p.yolo_profile_id === effectiveMode);
  const rawClosed = isShadow ? shadowClosedToday
    : isManual ? closedToday.filter((t) => t.source === "MANUAL")
    : closedToday.filter((t) => t.yolo_profile_id === effectiveMode);

  const openPositions = positionsMinConfidence > 0
    ? rawPositions.filter((p) => p.signal_confidence != null && Number(p.signal_confidence) >= positionsMinConfidence)
    : rawPositions;
  const closedTrades = positionsMinConfidence > 0
    ? rawClosed.filter((t) => t.signal_confidence != null && Number(t.signal_confidence) >= positionsMinConfidence)
    : rawClosed;

  // Number(): backend may serialize pnl as a string (Decimal); a bare `+` would
  // string-concatenate and render ₹0.00. Same reason as MobileTrades' summary.
  const closedPnl = closedTrades.reduce((s, t) => s + Number(t.pnl ?? 0), 0);

  return (
    <div className="p-3">
      {/* Source selector */}
      <div className="sticky top-0 z-10 -mx-3 px-3 pb-2 mb-2 bg-bg-primary border-b border-border">
        <div className="flex gap-1.5 overflow-x-auto">
          <button
            onClick={() => setDashboardViewMode("MANUAL")}
            className={`text-[12px] font-mono px-2.5 py-1 rounded border whitespace-nowrap ${
              isManual ? "bg-accent/10 text-accent border-accent/30" : "bg-bg-tertiary text-text-secondary border-border"
            }`}
          >Manual</button>
          {activeProfiles.map((p) => (
            <button
              key={p.id}
              onClick={() => setDashboardViewMode(p.id)}
              className={`text-[12px] font-mono px-2.5 py-1 rounded border whitespace-nowrap ${
                effectiveMode === p.id ? "bg-accent/10 text-accent border-accent/30" : "bg-bg-tertiary text-text-secondary border-border"
              }`}
            >{p.name}</button>
          ))}
          <button
            onClick={() => setDashboardViewMode("SHADOW")}
            className={`text-[12px] font-mono px-2.5 py-1 rounded border whitespace-nowrap ${
              isShadow ? "bg-shadowbook-bg/15 text-shadowbook border-shadowbook-bg/30" : "bg-bg-tertiary text-text-secondary border-border"
            }`}
          >Shadow</button>
        </div>
      </div>

      {/* Open */}
      <div className="flex items-center justify-between px-0.5 mb-1.5">
        <span className="text-[11px] font-mono text-text-muted uppercase tracking-wider">Open ({openPositions.length})</span>
      </div>
      {openPositions.length === 0 ? (
        <div className="py-4 text-center text-text-muted text-[13px] font-mono">{isShadow ? "no shadow positions" : "no open positions"}</div>
      ) : (
        openPositions.map((p) => <OpenPositionCard key={p.id} pos={p} prices={prices} isShadow={isShadow} onClose={handleClose} />)
      )}

      {/* Closed today */}
      {closedTrades.length > 0 && (
        <>
          <div className="flex items-center justify-between px-0.5 mt-4 mb-1.5">
            <span className="text-[11px] font-mono text-text-muted uppercase tracking-wider">
              {isShadow ? "Shadow Closed Today" : "Closed Today"} ({closedTrades.length})
            </span>
            <span className={`text-[12px] font-mono ${pnlColor(closedPnl)}`}>{formatINR(closedPnl)}</span>
          </div>
          {closedTrades.map((t) => <ClosedTradeCard key={t.id} t={t} />)}
        </>
      )}
    </div>
  );
}

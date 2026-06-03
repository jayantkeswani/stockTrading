"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useStore } from "@/store";
import { useShallow } from "zustand/react/shallow";
import { formatINR, formatPercent, pnlColor, startOfDayIST, endOfDayIST, startOfWeekIST, subDaysIST } from "@/lib/formatters";
import { STRATEGY_LABELS } from "@/lib/constants";
import type { Trade } from "@/lib/types";

type PeriodKey = "today" | "yesterday" | "week" | "30d" | "custom";

const PERIOD_LABELS: { key: PeriodKey; label: string }[] = [
  { key: "today", label: "Today" },
  { key: "yesterday", label: "Yesterday" },
  { key: "week", label: "Week" },
  { key: "30d", label: "30 Days" },
  { key: "custom", label: "Custom" },
];

function rangeFor(key: PeriodKey, customStart: string, customEnd: string): { start: Date; end: Date } | null {
  const now = new Date();
  switch (key) {
    case "today": return { start: startOfDayIST(now), end: endOfDayIST(now) };
    case "yesterday": { const y = subDaysIST(now, 1); return { start: startOfDayIST(y), end: endOfDayIST(y) }; }
    case "week": return { start: startOfWeekIST(now), end: endOfDayIST(now) };
    case "30d": return { start: startOfDayIST(subDaysIST(now, 30)), end: endOfDayIST(now) };
    case "custom":
      if (!customStart || !customEnd) return null;
      return { start: new Date(`${customStart}T00:00:00+05:30`), end: new Date(`${customEnd}T23:59:59+05:30`) };
  }
}

function formatISTTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleTimeString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", hour12: false });
}
function formatISTDate(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short" });
}

function KV({ label, value, cls }: { label: string; value: string; cls?: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[9px] font-mono text-text-muted tracking-wide">{label}</span>
      <span className={`text-[12px] font-mono font-medium mt-0.5 ${cls ?? ""}`}>{value}</span>
    </div>
  );
}

function ScoreCard({ k, v, cls }: { k: string; v: string; cls?: string }) {
  return (
    <div className="flex-1 rounded-lg border border-border bg-bg-secondary px-2 py-2 text-center">
      <div className="text-[9px] font-mono text-text-muted tracking-wide">{k}</div>
      <div className={`text-[15px] font-mono font-bold mt-1 ${cls ?? ""}`}>{v}</div>
    </div>
  );
}

function TradeCard({ t }: { t: Trade }) {
  const [open, setOpen] = useState(false);
  const isFutures = !t.option_type;
  const long = t.side === "BUY";
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
          <span className="text-[10px] font-mono px-1 py-px rounded bg-accent/10 text-accent">{STRATEGY_LABELS[t.strategy_name] || t.strategy_name}</span>
          <span className={`ml-auto text-[14px] font-mono font-bold ${pnlColor(t.pnl ?? 0)}`}>{formatINR(t.pnl ?? 0)}</span>
          <span className={`text-text-muted text-[13px] transition-transform ${open ? "rotate-180" : ""}`}>▼</span>
        </div>
        <div className="flex items-center gap-3 mt-1.5 text-[11px] font-mono text-text-muted">
          <span>{formatINR(t.entry_price)} → {t.exit_price ? formatINR(t.exit_price) : "—"}</span>
          <span>{t.lots}L ({t.quantity})</span>
          <span>{formatISTDate(t.entry_time)} {formatISTTime(t.entry_time)}</span>
          <span className={`ml-auto ${t.exit_reason === "TRAILING_SL" || t.exit_reason === "INVALIDATION" ? "text-warning" : ""}`}>
            {t.exit_reason?.replace(/_/g, " ") ?? "—"}
          </span>
        </div>
      </button>
      {open && (
        <div className="px-3 py-2.5 border-t border-border bg-bg-primary animate-fade-in">
          <div className="grid grid-cols-3 gap-y-2.5 gap-x-3">
            <KV label="Side" value={t.side} />
            <KV label="SL / Tgt" value={`${t.stop_loss ? formatINR(t.stop_loss) : "—"} / ${t.target_price ? formatINR(t.target_price) : "—"}`} />
            <KV label="Net P&L" value={t.net_pnl != null ? formatINR(Number(t.net_pnl)) : "—"} cls={pnlColor(t.net_pnl ?? 0)} />
            <KV label="Charges" value={t.charges_json ? formatINR(t.charges_json.total) : "—"} cls="text-text-secondary" />
            <KV label="Margin" value={t.margin_required ? formatINR(Number(t.margin_required)) : "—"} />
            <KV label="Source" value={t.source} />
            <KV label="Conf" value={t.signal_confidence != null ? String(t.signal_confidence) : "—"} cls="text-accent" />
            <KV label="P&L %" value={t.pnl_percent != null ? formatPercent(t.pnl_percent) : "—"} cls={pnlColor(t.pnl ?? 0)} />
            <KV label="Exit → " value={`${formatISTTime(t.exit_time)}`} />
          </div>
          {t.signal_ai_summary && (
            <p className="text-[12px] font-mono text-text-primary leading-relaxed mt-2.5">
              <span className="text-accent">✦</span> {t.signal_ai_summary}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

/**
 * Mobile Trades tab: period filter (Today / Yesterday / Week / 30 Days /
 * Custom) + source selector (Manual / YOLO profiles / Shadow), a summary strip
 * (net P&L, win rate, count), and expandable closed-trade cards. Fetches
 * CLOSED trades only. Used by: MobileShell.
 */
export function MobileTrades({ refreshKey }: { refreshKey?: number }) {
  const { tradesViewMode, setTradesViewMode, showNetPnL, yoloProfiles } = useStore(useShallow((s) => ({
    tradesViewMode: s.tradesViewMode, setTradesViewMode: s.setTradesViewMode,
    showNetPnL: s.showNetPnL, yoloProfiles: s.yoloProfiles,
  })));

  const [periodKey, setPeriodKey] = useState<PeriodKey>("today");
  const [customStart, setCustomStart] = useState("");
  const [customEnd, setCustomEnd] = useState("");
  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);

  const activeProfiles = useMemo(
    () => (Array.isArray(yoloProfiles) ? yoloProfiles : []).filter((p) => p.is_active).sort((a, b) => a.sort_order - b.sort_order),
    [yoloProfiles],
  );
  const effectiveMode = useMemo(() => {
    if (tradesViewMode === "SHADOW") return "SHADOW";
    if (tradesViewMode === "MANUAL") return "MANUAL";
    if ((Array.isArray(yoloProfiles) ? yoloProfiles : []).some((p) => p.id === tradesViewMode)) return tradesViewMode;
    return activeProfiles[0]?.id ?? "MANUAL";
  }, [tradesViewMode, yoloProfiles, activeProfiles]);
  const isShadow = effectiveMode === "SHADOW";
  const isManual = effectiveMode === "MANUAL";

  const range = useMemo(() => rangeFor(periodKey, customStart, customEnd), [periodKey, customStart, customEnd]);

  useEffect(() => {
    if (!range) return;
    let cancelled = false;
    setLoading(true);
    async function load() {
      try {
        const data = (await api.getTrades({
          entry_since: range!.start.toISOString(),
          entry_until: range!.end.toISOString(),
          status: "CLOSED",
          limit: 1000,
          source: isShadow ? "SHADOW" : isManual ? "MANUAL" : undefined,
          yolo_profile_id: !isShadow && !isManual ? effectiveMode : undefined,
        })) as Trade[];
        if (!cancelled) setTrades(data);
      } catch {
        if (!cancelled) setTrades([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [range, effectiveMode, isShadow, isManual, refreshKey]);

  const summary = useMemo(() => {
    // Coerce with Number(): the backend may serialize pnl/net_pnl as strings
    // (Decimal), and `0 + "2125.00"` string-concatenates → the summed total
    // renders as ₹0.00 even though per-trade cards (which Number-coerce via
    // formatINR) look correct.
    const total = trades.reduce((s, t) => s + (showNetPnL && t.net_pnl != null ? Number(t.net_pnl) : Number(t.pnl ?? 0)), 0);
    const wins = trades.filter((t) => Number(t.pnl ?? 0) > 0).length;
    const losses = trades.filter((t) => Number(t.pnl ?? 0) < 0).length;
    const rate = wins + losses > 0 ? Math.round((wins / (wins + losses)) * 100) : 0;
    return { total, wins, losses, rate, count: trades.length };
  }, [trades, showNetPnL]);

  return (
    <div className="p-3">
      {/* Period + source */}
      <div className="sticky top-0 z-10 -mx-3 px-3 pb-2 mb-2 bg-bg-primary border-b border-border space-y-2">
        <div className="flex gap-1.5 overflow-x-auto">
          {PERIOD_LABELS.map((p) => (
            <button
              key={p.key}
              onClick={() => setPeriodKey(p.key)}
              className={`text-[12px] font-mono px-2.5 py-1 rounded border whitespace-nowrap ${
                periodKey === p.key ? "bg-accent/10 text-accent border-accent/30" : "bg-bg-tertiary text-text-secondary border-border"
              }`}
            >{p.label}</button>
          ))}
        </div>
        {periodKey === "custom" && (
          <div className="flex items-center gap-2">
            <input type="date" value={customStart} onChange={(e) => setCustomStart(e.target.value)}
              className="text-[11px] font-mono bg-bg-tertiary border border-border rounded px-2 py-1 text-text-primary" />
            <span className="text-text-muted text-[11px]">→</span>
            <input type="date" value={customEnd} onChange={(e) => setCustomEnd(e.target.value)}
              className="text-[11px] font-mono bg-bg-tertiary border border-border rounded px-2 py-1 text-text-primary" />
          </div>
        )}
        <div className="flex gap-1.5 overflow-x-auto">
          <button onClick={() => setTradesViewMode("MANUAL")}
            className={`text-[11px] font-mono px-2 py-0.5 rounded border whitespace-nowrap ${isManual ? "bg-accent/10 text-accent border-accent/30" : "bg-bg-tertiary text-text-secondary border-border"}`}>Manual</button>
          {activeProfiles.map((p) => (
            <button key={p.id} onClick={() => setTradesViewMode(p.id)}
              className={`text-[11px] font-mono px-2 py-0.5 rounded border whitespace-nowrap ${effectiveMode === p.id ? "bg-accent/10 text-accent border-accent/30" : "bg-bg-tertiary text-text-secondary border-border"}`}>{p.name}</button>
          ))}
          <button onClick={() => setTradesViewMode("SHADOW")}
            className={`text-[11px] font-mono px-2 py-0.5 rounded border whitespace-nowrap ${isShadow ? "bg-shadowbook-bg/15 text-shadowbook border-shadowbook-bg/30" : "bg-bg-tertiary text-text-secondary border-border"}`}>Shadow</button>
        </div>
      </div>

      {/* Summary */}
      <div className="flex gap-1.5 mb-2.5">
        <ScoreCard k={showNetPnL ? "NET P&L" : "P&L"} v={formatINR(summary.total)} cls={pnlColor(summary.total)} />
        <ScoreCard k="WIN RATE" v={`${summary.wins}W/${summary.losses}L · ${summary.rate}%`} />
        <ScoreCard k="TRADES" v={String(summary.count)} />
      </div>

      {loading ? (
        <div className="py-8 text-center text-text-muted text-[13px] font-mono">loading…</div>
      ) : trades.length === 0 ? (
        <div className="py-8 text-center text-text-muted text-[13px] font-mono">no closed trades</div>
      ) : (
        trades.map((t) => <TradeCard key={t.id} t={t} />)
      )}
    </div>
  );
}

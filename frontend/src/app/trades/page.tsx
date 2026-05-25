"use client";

import { useEffect, useState, useMemo, useCallback } from "react";
import { api } from "@/lib/api";
import { startOfDayIST, endOfDayIST, startOfWeekIST, subDaysIST, subMonthsIST, isoDateIST, formatINR } from "@/lib/formatters";
import { STRATEGY_LABELS } from "@/lib/constants";
import type { Trade } from "@/lib/types";
import { PeriodFilter, type Period } from "@/components/trades/PeriodFilter";
import { SummaryStrip } from "@/components/trades/SummaryStrip";
import { PnLHeatmap } from "@/components/trades/PnLHeatmap";
import { TradesTable } from "@/components/trades/TradesTable";
import { useStore } from "@/store";

function periodFromLabel(label: string): Period {
  const now = new Date();
  switch (label) {
    case "Today":     return { start: startOfDayIST(now), end: endOfDayIST(now), label };
    case "This Week": return { start: startOfWeekIST(now), end: endOfDayIST(now), label };
    case "Last 3M":   return { start: startOfDayIST(subMonthsIST(now, 3)), end: endOfDayIST(now), label };
    default:          return { start: startOfDayIST(subDaysIST(now, 30)), end: endOfDayIST(now), label: "Last 30D" };
  }
}

function toggleItem(arr: string[], item: string): string[] {
  return arr.includes(item) ? arr.filter((x) => x !== item) : [...arr, item];
}

type HoldResultMap = Map<string, { max_high: number | null; min_low: number | null }>;

function applySimLots(trade: Trade, simLots: number | null): Trade {
  if (simLots == null || trade.pnl == null || trade.lots == null || trade.lots === 0) return trade;
  const ratio = simLots / trade.lots;
  const scaledPnl = (Number(trade.pnl) / trade.lots) * simLots;
  const scaledCharges = trade.charges_json
    ? {
        ...trade.charges_json,
        brokerage: trade.charges_json.brokerage * ratio,
        stt: trade.charges_json.stt * ratio,
        exchange_txn: trade.charges_json.exchange_txn * ratio,
        gst: trade.charges_json.gst * ratio,
        sebi_charges: trade.charges_json.sebi_charges * ratio,
        stamp_duty: trade.charges_json.stamp_duty * ratio,
        total: trade.charges_json.total * ratio,
      }
    : null;
  return {
    ...trade,
    pnl: scaledPnl,
    net_pnl: trade.net_pnl != null ? scaledPnl - (scaledCharges?.total ?? 0) : null,
    charges_json: scaledCharges,
    margin_required: trade.margin_required != null ? Number(trade.margin_required) * ratio : null,
  };
}

function effectivePnl(trade: Trade, showNet: boolean): number | null {
  if (trade.pnl == null) return null;
  if (showNet && trade.net_pnl != null) return Number(trade.net_pnl);
  return Number(trade.pnl);
}

function applyHoldAnalysis(
  trade: Trade,
  holdMap: HoldResultMap,
  scenario: "best" | "worst"
): Trade {
  if (trade.status !== "CLOSED" || trade.exit_price == null) return trade;
  const result = holdMap.get(trade.id);
  if (!result) return trade;

  const hypoExit = scenario === "best" ? result.max_high : result.min_low;
  if (hypoExit == null) return trade;

  const entry = Number(trade.entry_price);
  const qty = Number(trade.quantity);
  if (entry === 0 || qty === 0) return trade;

  const diff = trade.side === "SELL" ? (entry - hypoExit) : (hypoExit - entry);
  const hypoPnl = diff * qty;
  const hypoPnlPct = (diff / entry) * 100;

  return {
    ...trade,
    pnl: hypoPnl,
    pnl_percent: hypoPnlPct,
    net_pnl: null,
  };
}

function computeMarginTimeline(trades: Trade[]): { peak_margin: number; peak_time: string | null; total_margin: number; trade_count: number } | null {
  const withMargin = trades.filter((t) => t.margin_required != null && Number(t.margin_required) > 0);
  if (withMargin.length === 0) return null;
  const events: { ts: number; delta: number }[] = [];
  for (const t of withMargin) {
    const m = Number(t.margin_required);
    events.push({ ts: new Date(t.entry_time).getTime(), delta: m });
    if (t.exit_time) events.push({ ts: new Date(t.exit_time).getTime(), delta: -m });
  }
  events.sort((a, b) => a.ts - b.ts);
  let running = 0;
  let peak = 0;
  let peakTs: number | null = null;
  for (const e of events) {
    running += e.delta;
    if (running > peak) { peak = running; peakTs = e.ts; }
  }
  return {
    peak_margin: peak,
    peak_time: peakTs != null ? new Date(peakTs).toISOString() : null,
    total_margin: withMargin.reduce((s, t) => s + Number(t.margin_required), 0),
    trade_count: withMargin.length,
  };
}

function computePeakMargin(trades: Trade[]): number | undefined {
  const result = computeMarginTimeline(trades);
  return result && result.peak_margin > 0 ? result.peak_margin : undefined;
}

function isSimActive(sim: { min_confidence: number; ai_action: string; instrument_type: string; signal_types: string[]; sim_lots: number | null }): boolean {
  return sim.min_confidence > 0 || sim.ai_action !== "" || sim.instrument_type !== "" || sim.signal_types.length > 0 || sim.sim_lots !== null;
}

function FilterPill({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      className={`px-2 py-1 rounded text-[9px] font-mono font-medium tracking-wide transition-all border whitespace-nowrap ${
        active
          ? "border-accent bg-accent/10 text-accent"
          : "border-border text-text-muted hover:border-border/80 hover:text-text-secondary"
      }`}
    >
      {label}
    </button>
  );
}

export default function TradesPage() {
  const {
    positionViewMode, setPositionViewMode,
    tradesPeriodLabel, tradesPeriodStart, tradesPeriodEnd, setTradesPeriod,
    tradesStrategy, setTradesStrategy,
    tradesSimOpen, setTradesSimOpen,
    tradesSim, setTradesSim, resetTradesSim,
    tradesHoldOpen, setTradesHoldOpen,
    tradesHold, setTradesHold, resetTradesHold,
    showNetPnL, setShowNetPnL,
    tradesShowOpen, setTradesShowOpen,
    tradesExcludePinned, setTradesExcludePinned,
  } = useStore();

  const [trades, setTrades] = useState<Trade[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedDay, setSelectedDay] = useState<string | null>(null);
  const [marginResult, setMarginResult] = useState<{ peak_margin: number; peak_time: string | null; total_margin: number; trade_count: number } | null>(null);
  const [holdMap, setHoldMap] = useState<HoldResultMap>(new Map());
  const [holdLoading, setHoldLoading] = useState(false);

  const mode = positionViewMode === "SHADOW" ? "SHADOW" : "REAL";
  const sim = tradesSim;
  const simOpen = tradesSimOpen;
  const simActive = simOpen && isSimActive(sim);

  const hold = tradesHold;
  const holdOpen = tradesHoldOpen;
  const holdActive = holdOpen;

  // Derive Period object from store
  const period = useMemo((): Period => {
    if (tradesPeriodLabel === "Custom" && tradesPeriodStart && tradesPeriodEnd) {
      return { label: "Custom", start: new Date(tradesPeriodStart), end: new Date(tradesPeriodEnd) };
    }
    return periodFromLabel(tradesPeriodLabel || "Last 30D");
  }, [tradesPeriodLabel, tradesPeriodStart, tradesPeriodEnd]);

  useEffect(() => {
    setSelectedDay(null);
    setLoading(true);
    let cancelled = false;
    async function load() {
      try {
        const data = (await api.getTrades({
          entry_since: period.start.toISOString(),
          entry_until: period.end.toISOString(),
          limit: 1000,
          source: mode === "SHADOW" ? "SHADOW" : undefined,
          strategy: tradesStrategy || undefined,
          status: tradesShowOpen ? undefined : "CLOSED",
          exclude_permanent: tradesExcludePinned || undefined,
          ...(simOpen
            ? {
                min_confidence: sim.min_confidence > 0 ? sim.min_confidence : undefined,
                ai_action: sim.ai_action || undefined,
                instrument_type: sim.instrument_type || undefined,
              }
            : {}),
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
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [period.start, period.end, mode, tradesStrategy, tradesShowOpen, tradesExcludePinned, simOpen, sim.min_confidence, sim.ai_action, sim.instrument_type]);

  useEffect(() => {
    if (!holdOpen) {
      setHoldMap(new Map());
      return;
    }
    const closedIds = trades.filter((t) => t.status === "CLOSED").map((t) => t.id);
    if (closedIds.length === 0) {
      setHoldMap(new Map());
      return;
    }
    let cancelled = false;
    setHoldLoading(true);
    api
      .holdAnalysis(closedIds, hold.scenario)
      .then((res) => {
        if (cancelled) return;
        const m: HoldResultMap = new Map();
        for (const r of res.results) {
          m.set(r.trade_id, { max_high: r.max_high, min_low: r.min_low });
        }
        setHoldMap(m);
      })
      .catch(() => {
        if (!cancelled) setHoldMap(new Map());
      })
      .finally(() => {
        if (!cancelled) setHoldLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [holdOpen, hold.scenario, trades]);

  const filteredTrades = useMemo(() => {
    if (!simOpen || sim.signal_types.length === 0) return trades;
    return trades.filter((t) => t.signal_type != null && sim.signal_types.includes(t.signal_type));
  }, [trades, simOpen, sim.signal_types]);

  const simTrades = useMemo(() => {
    if (!simOpen || sim.sim_lots == null) return filteredTrades;
    return filteredTrades.map((t) => applySimLots(t, sim.sim_lots));
  }, [filteredTrades, simOpen, sim.sim_lots]);

  const holdTrades = useMemo(() => {
    if (!holdOpen || holdMap.size === 0) return simTrades;
    return simTrades.map((t) => applyHoldAnalysis(t, holdMap, hold.scenario));
  }, [simTrades, holdOpen, holdMap, hold.scenario]);

  const dailyPnL = useMemo(() => {
    const map = new Map<string, number>();
    for (const t of holdTrades) {
      const pnl = effectivePnl(t, showNetPnL);
      if (t.status !== "CLOSED" || pnl == null) continue;
      const key = isoDateIST(new Date(t.entry_time));
      map.set(key, (map.get(key) ?? 0) + pnl);
    }
    return map;
  }, [holdTrades, showNetPnL]);

  const displayedTrades = useMemo(() => {
    if (!selectedDay) return holdTrades;
    return holdTrades.filter((t) => isoDateIST(new Date(t.entry_time)) === selectedDay);
  }, [holdTrades, selectedDay]);

  const peakMargin = useMemo(() => {
    return computePeakMargin(holdTrades);
  }, [holdTrades]);

  const handleMarginAnalysis = useCallback(() => {
    if (displayedTrades.length === 0) return;
    setMarginResult(computeMarginTimeline(displayedTrades));
  }, [displayedTrades]);

  const periodLabel = `${period.start.toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata", day: "2-digit", month: "short",
  })} — ${period.end.toLocaleDateString("en-IN", {
    timeZone: "Asia/Kolkata", day: "2-digit", month: "short", year: "numeric",
  })}`;

  return (
    <div className="space-y-2">
      {/* Filter bar */}
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <PeriodFilter
            key={tradesPeriodLabel}
            value={period}
            onChange={(p) => setTradesPeriod(p.label, p.start, p.end)}
          />
          <select
            value={tradesStrategy}
            onChange={(e) => setTradesStrategy(e.target.value)}
            className="text-[10px] font-mono bg-bg-tertiary border border-border rounded px-1.5 py-1 text-text-secondary"
          >
            <option value="">All Strategies</option>
            {Object.entries(STRATEGY_LABELS).map(([key, label]) => (
              <option key={key} value={key}>{label}</option>
            ))}
          </select>
          {/* Real / Shadow toggle */}
          <div className="flex items-center rounded border border-border overflow-hidden text-[10px] font-mono">
            <button
              onClick={() => setPositionViewMode("REAL")}
              className={`px-2 py-1 transition-colors ${
                mode === "REAL" ? "bg-accent/15 text-accent" : "text-text-muted hover:text-text-secondary"
              }`}
            >
              Real
            </button>
            <button
              onClick={() => setPositionViewMode("SHADOW")}
              className={`px-2 py-1 border-l border-border transition-colors ${
                mode === "SHADOW" ? "bg-purple-500/15 text-purple-400" : "text-text-muted hover:text-text-secondary"
              }`}
            >
              Shadow
            </button>
          </div>
          {/* Net P&L toggle */}
          <button
            onClick={() => setShowNetPnL(!showNetPnL)}
            className={`px-2 py-1 rounded border text-[10px] font-mono transition-colors ${
              showNetPnL
                ? "border-accent/50 bg-accent/10 text-accent"
                : "border-border text-text-muted hover:text-text-secondary"
            }`}
          >
            Net P&amp;L
          </button>
          {/* Show Open trades toggle */}
          <button
            onClick={() => setTradesShowOpen(!tradesShowOpen)}
            className={`px-2 py-1 rounded border text-[10px] font-mono transition-colors ${
              tradesShowOpen
                ? "border-accent/50 bg-accent/10 text-accent"
                : "border-border text-text-muted hover:text-text-secondary"
            }`}
          >
            + Open
          </button>
          {/* Exclude Pinned toggle */}
          <button
            onClick={() => setTradesExcludePinned(!tradesExcludePinned)}
            className={`px-2 py-1 rounded border text-[10px] font-mono transition-colors ${
              tradesExcludePinned
                ? "border-accent/50 bg-accent/10 text-accent"
                : "border-border text-text-muted hover:text-text-secondary"
            }`}
          >
            − Pinned
          </button>
          {/* Sim toggle */}
          <button
            onClick={() => setTradesSimOpen(!simOpen)}
            className={`px-2 py-1 rounded border text-[10px] font-mono transition-colors ${
              simActive
                ? "border-accent/50 bg-accent/10 text-accent"
                : simOpen
                ? "border-border/60 text-text-secondary"
                : "border-border text-text-muted hover:text-text-secondary"
            }`}
          >
            Sim {simOpen ? "▲" : "▾"}
          </button>
          {/* Hold Analysis toggle */}
          <button
            onClick={() => setTradesHoldOpen(!holdOpen)}
            className={`px-2 py-1 rounded border text-[10px] font-mono transition-colors ${
              holdActive
                ? "border-accent/50 bg-accent/10 text-accent"
                : holdOpen
                ? "border-border/60 text-text-secondary"
                : "border-border text-text-muted hover:text-text-secondary"
            }`}
          >
            Hold {holdOpen ? "▲" : "▾"}
          </button>
        </div>
        <span className="text-[9px] font-mono text-text-muted/40 tracking-wider">{periodLabel}</span>
      </div>

      {/* Simulation filter panel */}
      {simOpen && (
        <div className="rounded border border-accent/20 bg-bg-secondary p-4 space-y-4">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <span className="text-[11px] font-mono font-semibold uppercase tracking-widest text-text-secondary">
                Simulation Filters
              </span>
              {simActive && (
                <span className="text-[8px] font-mono px-1.5 py-0.5 rounded border border-accent/60 text-accent tracking-widest uppercase">
                  Active
                </span>
              )}
            </div>
            <button
              onClick={resetTradesSim}
              className="text-[10px] font-mono px-3 py-1.5 rounded border border-border text-text-muted hover:border-accent/40 hover:text-accent transition-colors"
            >
              Reset all
            </button>
          </div>

          <div className="grid grid-cols-5 gap-6">
            {/* Min Confidence */}
            <div className="space-y-2">
              <span className="text-[9px] font-mono uppercase tracking-widest text-text-muted">Min Confidence</span>
              <div className="text-sm font-mono font-medium text-accent leading-none">
                {sim.min_confidence > 0 ? `${sim.min_confidence}% and above` : "Any"}
              </div>
              <input
                type="range" min={0} max={100} step={5}
                value={sim.min_confidence}
                onChange={(e) => setTradesSim({ min_confidence: Number(e.target.value) })}
                className="w-full h-1.5 accent-amber-500 cursor-pointer"
              />
              <div className="flex justify-between text-[8px] font-mono text-text-muted/40">
                <span>0</span><span>50</span><span>100</span>
              </div>
            </div>

            {/* AI Action */}
            <div className="space-y-2">
              <span className="text-[9px] font-mono uppercase tracking-widest text-text-muted">AI Action</span>
              <div className="grid grid-cols-2 gap-1">
                {(["PROCEED", "RECONSIDER", "SKIP", ""] as const).map((val) => (
                  <FilterPill
                    key={val || "any"}
                    label={val || "Any"}
                    active={sim.ai_action === val}
                    onClick={() => setTradesSim({ ai_action: sim.ai_action === val ? "" : val })}
                  />
                ))}
              </div>
            </div>

            {/* Simulate Lots */}
            <div className="space-y-2">
              <span className="text-[9px] font-mono uppercase tracking-widest text-text-muted">Simulate Lots</span>
              <div className="text-[9px] font-mono text-text-muted/60 leading-tight">
                Recalculate P&amp;L as if every trade used N lots
              </div>
              <div className="flex items-center gap-2 mt-1">
                <button
                  onClick={() => setTradesSim({ sim_lots: Math.max(1, (sim.sim_lots ?? 1) - 1) })}
                  className="w-6 h-6 rounded border border-border text-text-muted hover:border-accent/40 hover:text-accent font-mono text-sm leading-none transition-colors flex items-center justify-center"
                >
                  −
                </button>
                <span className={`text-lg font-mono font-semibold w-8 text-center ${sim.sim_lots != null ? "text-accent" : "text-text-muted/40"}`}>
                  {sim.sim_lots ?? "—"}
                </span>
                <button
                  onClick={() => setTradesSim({ sim_lots: (sim.sim_lots ?? 0) + 1 })}
                  className="w-6 h-6 rounded border border-border text-text-muted hover:border-accent/40 hover:text-accent font-mono text-sm leading-none transition-colors flex items-center justify-center"
                >
                  +
                </button>
                {sim.sim_lots != null && (
                  <button
                    onClick={() => setTradesSim({ sim_lots: null })}
                    className="text-[9px] font-mono text-text-muted/40 hover:text-text-muted ml-1 transition-colors"
                  >
                    actual
                  </button>
                )}
              </div>
            </div>

            {/* Instrument Type */}
            <div className="space-y-2">
              <span className="text-[9px] font-mono uppercase tracking-widest text-text-muted">Instrument Type</span>
              <div className="grid grid-cols-2 gap-1">
                {(["FUTURE", "OPTION", "EQUITY"] as const).map((val) => (
                  <FilterPill
                    key={val}
                    label={val}
                    active={sim.instrument_type === val}
                    onClick={() => setTradesSim({ instrument_type: sim.instrument_type === val ? "" : val })}
                  />
                ))}
              </div>
            </div>

            {/* Signal Type */}
            <div className="space-y-2">
              <span className="text-[9px] font-mono uppercase tracking-widest text-text-muted">Signal Type</span>
              <div className="grid grid-cols-2 gap-1">
                {(["BUY_FUT", "SELL_FUT", "BUY_CE", "BUY_PE"] as const).map((val) => (
                  <FilterPill
                    key={val}
                    label={val.replace("_", " ")}
                    active={sim.signal_types.includes(val)}
                    onClick={() => setTradesSim({ signal_types: toggleItem(sim.signal_types, val) })}
                  />
                ))}
              </div>
            </div>
          </div>

          {simActive && (
            <p className="text-[9px] font-mono text-text-muted/40">
              {sim.sim_lots != null && `P&L recalculated as if every trade used ${sim.sim_lots} lot${sim.sim_lots !== 1 ? "s" : ""}. `}
              Confidence / AI action / instrument filters exclude trades with no linked signal. Signal type filtered client-side.
            </p>
          )}
        </div>
      )}

      {/* Hold Analysis panel */}
      {holdOpen && (
        <div className="rounded border border-accent/20 bg-bg-secondary p-4 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2.5">
              <span className="text-[11px] font-mono font-semibold uppercase tracking-widest text-text-secondary">
                Hold Analysis
              </span>
              {holdLoading && (
                <span className="text-[8px] font-mono px-1.5 py-0.5 rounded border border-accent/60 text-accent tracking-widest uppercase animate-pulse">
                  Loading
                </span>
              )}
              {!holdLoading && holdMap.size > 0 && (
                <span className="text-[8px] font-mono px-1.5 py-0.5 rounded border border-accent/60 text-accent tracking-widest uppercase">
                  Active
                </span>
              )}
            </div>
            <button
              onClick={resetTradesHold}
              className="text-[10px] font-mono px-3 py-1.5 rounded border border-border text-text-muted hover:border-accent/40 hover:text-accent transition-colors"
            >
              Reset
            </button>
          </div>

          <div className="space-y-2">
            <span className="text-[9px] font-mono uppercase tracking-widest text-text-muted">
              Scenario
            </span>
            <div className="flex gap-1">
              <FilterPill
                label="Best Case"
                active={hold.scenario === "best"}
                onClick={() => setTradesHold({ scenario: "best" })}
              />
              <FilterPill
                label="Worst Case"
                active={hold.scenario === "worst"}
                onClick={() => setTradesHold({ scenario: "worst" })}
              />
            </div>
          </div>

          <p className="text-[9px] font-mono text-text-muted/40">
            P&L recomputed using{" "}
            {hold.scenario === "best" ? "max HIGH" : "min LOW"} from 1m candles
            between actual exit and 15:30 IST same day. Charges unchanged; net P&L
            falls back to gross.
          </p>
        </div>
      )}

      {mode === "SHADOW" && (
        <div className="px-3 py-1.5 rounded border border-purple-500/20 bg-purple-500/5">
          <p className="text-[10px] font-mono text-purple-400/70">
            Shadow mode — showing shadow trades that execute every signal (including blocked ones) to measure raw signal accuracy. These do not affect P&amp;L, risk, or active positions.
          </p>
        </div>
      )}

      <SummaryStrip trades={holdTrades} dailyPnL={dailyPnL} showNetPnL={showNetPnL} peakMargin={peakMargin} />

      <PnLHeatmap
        period={period}
        dailyPnL={dailyPnL}
        selectedDay={selectedDay}
        onSelectDay={setSelectedDay}
      />

      {/* Margin Analysis */}
      <div className="rounded border border-border bg-bg-secondary px-3 py-2 flex items-center gap-4">
        <span className="text-[9px] font-mono uppercase tracking-wider text-text-muted">Margin Analysis</span>
        <button
          onClick={handleMarginAnalysis}
          disabled={displayedTrades.length === 0}
          className="px-2 py-1 rounded border text-[10px] font-mono transition-colors border-border text-text-muted hover:border-accent/40 hover:text-accent disabled:opacity-40 disabled:cursor-not-allowed"
        >
          Analyze Margin
        </button>
        {marginResult && (
          <>
            <div className="w-px h-4 bg-border" />
            <div className="flex items-center gap-4">
              <div className="flex flex-col">
                <span className="text-[8px] font-mono uppercase tracking-wider text-text-muted/50">Peak Concurrent</span>
                <span className="text-xs font-mono font-medium text-accent">{formatINR(marginResult.peak_margin)}</span>
              </div>
              <div className="flex flex-col">
                <span className="text-[8px] font-mono uppercase tracking-wider text-text-muted/50">Total Margin</span>
                <span className="text-xs font-mono font-medium text-text-secondary">{formatINR(marginResult.total_margin)}</span>
              </div>
              <div className="flex flex-col">
                <span className="text-[8px] font-mono uppercase tracking-wider text-text-muted/50">Trades</span>
                <span className="text-xs font-mono font-medium text-text-secondary">{marginResult.trade_count}</span>
              </div>
              {marginResult.peak_time && (
                <div className="flex flex-col">
                  <span className="text-[8px] font-mono uppercase tracking-wider text-text-muted/50">Peak At</span>
                  <span className="text-xs font-mono text-text-muted">
                    {new Date(marginResult.peak_time).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", hour: "2-digit", minute: "2-digit", day: "2-digit", month: "short" })}
                  </span>
                </div>
              )}
            </div>
          </>
        )}
      </div>

      <div
        className={`rounded border border-border bg-bg-secondary overflow-hidden transition-opacity ${
          holdLoading ? "opacity-60 pointer-events-none" : ""
        }`}
      >
        {selectedDay && (
          <div className="px-3 py-1 border-b border-border/30 flex items-center gap-2 bg-bg-tertiary/30">
            <span className="text-[8px] font-mono uppercase tracking-wider text-text-muted/50">Day</span>
            <span className="text-[9px] font-mono text-accent">{selectedDay}</span>
            <span className="text-[8px] font-mono text-text-muted/40">
              · {displayedTrades.length} trade{displayedTrades.length !== 1 ? "s" : ""}
            </span>
            <button
              onClick={() => setSelectedDay(null)}
              className="ml-auto text-[8px] font-mono text-text-muted/40 hover:text-text-muted transition-colors"
            >
              ✕ show all
            </button>
          </div>
        )}
        <TradesTable
          trades={displayedTrades}
          loading={loading}
          showSource={mode === "SHADOW"}
          showSignalData={simActive}
          simLots={simOpen ? sim.sim_lots : null}
          showNetPnL={showNetPnL}
        />
      </div>
    </div>
  );
}

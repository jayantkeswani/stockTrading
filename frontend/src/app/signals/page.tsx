"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatINR, formatTime, formatDate, startOfWeekIST, startOfDayIST, endOfDayIST, subDaysIST, subMonthsIST } from "@/lib/formatters";
import { STRATEGY_LABELS, STATUS_COLORS } from "@/lib/constants";
import type { Signal } from "@/lib/types";
import { SignalHistoryPanel } from "@/components/signals/SignalHistoryPanel";
import { PeriodFilter, type Period } from "@/components/trades/PeriodFilter";
import { useStore } from "@/store";

function periodFromLabel(label: string, customStart?: string, customEnd?: string): Period {
  const now = new Date();
  switch (label) {
    case "Today":     return { start: startOfDayIST(now), end: endOfDayIST(now), label };
    case "Last 30D":  return { start: startOfDayIST(subDaysIST(now, 30)), end: endOfDayIST(now), label };
    case "Last 3M":   return { start: startOfDayIST(subMonthsIST(now, 3)), end: endOfDayIST(now), label };
    case "Custom":
      if (customStart && customEnd) {
        return { start: new Date(customStart), end: new Date(customEnd), label };
      }
      return { start: startOfWeekIST(now), end: endOfDayIST(now), label: "This Week" };
    default:          return { start: startOfWeekIST(now), end: endOfDayIST(now), label: "This Week" };
  }
}

const VWAP_CONFIDENCE_FACTOR_LABELS: Record<string, string> = {
  bias_alignment:       "Bias",
  vwap_slope_alignment: "Slope",
  reversal_quality:     "Reversal",
  volume_quality:       "Volume",
  rr_ratio_quality:     "R:R",
  oi_support:           "OI",
  cpr_narrow_trending:  "CPR",
  vix_regime:           "VIX",
  global_alignment:     "Global",
  time_of_day:          "Window",
};

const S5_CONFIDENCE_FACTOR_LABELS: Record<string, string> = {
  vol_factor:   "Volume",
  rvol_factor:  "RVOL",
  bias_factor:  "Nifty",
  phase_factor: "Phase",
  setup_factor: "Setup",
  rank_factor:  "Rank",
  gap_factor:   "Gap",
  trend_factor: "Trend",
  oi_factor:    "OI",
};

function SignalTypeBadge({ signalType }: { signalType: Signal["signal_type"] }) {
  const map: Record<string, { label: string; cls: string }> = {
    BUY_CE: { label: "▲ CE", cls: "text-profit" },
    BUY_PE: { label: "▼ PE", cls: "text-loss" },
    BUY_FUT: { label: "▲ FUT", cls: "text-accent" },
    SELL_FUT: { label: "▼ FUT", cls: "text-loss" },
  };
  const entry = map[signalType] || { label: signalType, cls: "text-text-muted" };
  return <span className={`text-xs font-mono font-bold ${entry.cls}`}>{entry.label}</span>;
}

function WindowBadge({ windowState }: { windowState?: string }) {
  if (!windowState) return null;
  const cfg: Record<string, { label: string; cls: string }> = {
    IN_WINDOW:      { label: "IN WINDOW",  cls: "bg-profit/10 text-profit border-profit/30" },
    DEAD_ZONE:      { label: "DEAD ZONE",  cls: "bg-warning/10 text-warning border-warning/30" },
    OUT_OF_WINDOW:  { label: "OFF WINDOW", cls: "bg-border/30 text-text-muted border-border/50" },
  };
  const c = cfg[windowState] || cfg["OUT_OF_WINDOW"];
  return (
    <span className={`text-[9px] font-mono px-1 py-px rounded border ${c.cls}`}>
      {c.label}
    </span>
  );
}

function ConfidenceFactorsBar({ factors, labelMap }: { factors: Record<string, number>; labelMap: Record<string, string> }) {
  const keys = Object.keys(labelMap).filter(k => factors[k] !== undefined);
  if (keys.length === 0) return null;
  return (
    <div className="mt-2">
      <p className="text-[9px] font-mono text-text-muted uppercase tracking-wider mb-1">Confidence Factors</p>
      <div className="flex flex-col gap-0.5">
        {keys.map(k => {
          const val = factors[k] as number;
          const pct = Math.round(val * 100);
          const color = val >= 0.7 ? "bg-profit/60" : val >= 0.4 ? "bg-accent/60" : "bg-loss/60";
          return (
            <div key={k} className="flex items-center gap-2">
              <span className="text-[9px] font-mono text-text-muted w-14 shrink-0">
                {labelMap[k]}
              </span>
              <div className="flex-1 h-1 bg-border/40 rounded-full overflow-hidden">
                <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
              </div>
              <span className="text-[9px] font-mono text-text-muted w-8 text-right">{pct}%</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function SignalCard({ signal }: { signal: Signal }) {
  const [expanded, setExpanded] = useState(false);
  const indicators = signal.indicators as Record<string, unknown>;
  const windowState = indicators?.window_state as string | undefined;
  const confFactors = indicators?.confidence_factors as Record<string, number> | undefined;
  const intradayBias = indicators?.intraday_bias as Record<string, unknown> | undefined;
  const aiKeySupports = indicators?.ai_key_supports as string[] | undefined;
  const aiKeyRisks = indicators?.ai_key_risks as string[] | undefined;
  const confLabelMap = signal.strategy_name === "intraday_futures" ? S5_CONFIDENCE_FACTOR_LABELS : VWAP_CONFIDENCE_FACTOR_LABELS;

  const isInformational = !signal.executable && signal.blocked_reason === "Outside trade window";

  return (
    <div className="px-3 py-2 hover:bg-bg-tertiary/30">
      {/* Header row */}
      <div className="flex items-center justify-between mb-1">
        <div className="flex items-center gap-2">
          <SignalTypeBadge signalType={signal.signal_type} />
          <span className="text-xs font-mono font-medium">{signal.symbol}</span>
          {signal.instrument_type === "OPTION" && signal.strike_price > 0 && (
            <span className="text-[10px] font-mono text-text-muted">
              {signal.strike_price} | {signal.expiry_date}
            </span>
          )}
          {windowState && <WindowBadge windowState={windowState} />}
          {isInformational && (
            <span className="text-[9px] font-mono text-text-muted italic">informational</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <span className={`text-[10px] font-mono px-1 py-px rounded ${STATUS_COLORS[signal.status] || ""}`}>
            {signal.status}
          </span>
          <span className="text-[10px] font-mono text-text-muted">
            {formatDate(signal.generated_at)} {formatTime(signal.generated_at)}
          </span>
        </div>
      </div>

      {/* AI summary — headline */}
      {signal.ai_summary ? (
        <p className="text-xs font-mono text-text-primary leading-relaxed mb-1">
          {signal.ai_summary}
        </p>
      ) : (
        <p className="text-xs font-mono text-text-muted leading-relaxed">{signal.reason}</p>
      )}

      {/* Metrics row */}
      <div className="mt-1 flex gap-3 text-xs font-mono text-text-muted flex-wrap items-center">
        {signal.instrument_type === "OPTION" && !signal.executable && !isInformational ? (
          <>
            <span className="text-warning">Idx: {formatINR(signal.entry_price)}</span>
            <span className="italic">premium n/a</span>
          </>
        ) : (
          <span>Entry: {formatINR(signal.entry_price)}</span>
        )}
        {signal.stop_loss > 0 && <span>SL: {formatINR(signal.stop_loss)}</span>}
        {signal.target_price && signal.target_price > 0 && (
          <span>Tgt: {formatINR(signal.target_price)}</span>
        )}
        {signal.confidence && (
          <span className={signal.confidence >= 70 ? "text-profit" : signal.confidence >= 55 ? "text-accent" : "text-text-muted"}>
            {signal.confidence}%
            {signal.ai_adjustment !== null && signal.ai_adjustment !== 0 && (
              <span className="text-[9px] text-text-muted ml-0.5">
                (AI {signal.ai_adjustment > 0 ? "+" : ""}{signal.ai_adjustment})
              </span>
            )}
          </span>
        )}
        {intradayBias && (
          <span className="text-[10px]">
            Bias: <span className={
              (intradayBias.score as number) > 0.2 ? "text-profit" :
              (intradayBias.score as number) < -0.2 ? "text-loss" : "text-text-muted"
            }>
              {((intradayBias.score as number) * 100).toFixed(0)}%
              {intradayBias.strength ? ` (${intradayBias.strength})` : ""}
            </span>
          </span>
        )}
        <span className="text-accent">
          {STRATEGY_LABELS[signal.strategy_name] || signal.strategy_name}
        </span>
        <button
          onClick={() => setExpanded(e => !e)}
          className="ml-auto text-[10px] font-mono text-text-muted hover:text-text-primary"
        >
          {expanded ? "▲ less" : "▼ more"}
        </button>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="mt-2 space-y-2 animate-fade-in">
          {/* AI action badge */}
          {signal.ai_action && signal.ai_action !== "PROCEED" && (
            <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded border ${
              signal.ai_action === "RECONSIDER"
                ? "bg-loss/10 text-loss border-loss/30"
                : "bg-warning/10 text-warning border-warning/30"
            }`}>
              AI: {signal.ai_action.replace("_", " ")}
            </span>
          )}

          {/* AI rationale */}
          {signal.ai_rationale && (
            <div>
              <p className="text-[9px] font-mono text-text-muted uppercase tracking-wider mb-0.5">AI Rationale</p>
              <p className="text-xs font-mono text-text-secondary leading-relaxed">{signal.ai_rationale}</p>
            </div>
          )}

          {/* Key supports & risks */}
          {(aiKeySupports?.length || aiKeyRisks?.length) ? (
            <div className="grid grid-cols-2 gap-2">
              {aiKeySupports && aiKeySupports.length > 0 && (
                <div>
                  <p className="text-[9px] font-mono text-profit uppercase tracking-wider mb-0.5">Supports</p>
                  <ul className="space-y-0.5">
                    {aiKeySupports.map((s, i) => (
                      <li key={i} className="text-[10px] font-mono text-text-muted">• {s}</li>
                    ))}
                  </ul>
                </div>
              )}
              {aiKeyRisks && aiKeyRisks.length > 0 && (
                <div>
                  <p className="text-[9px] font-mono text-loss uppercase tracking-wider mb-0.5">Risks</p>
                  <ul className="space-y-0.5">
                    {aiKeyRisks.map((r, i) => (
                      <li key={i} className="text-[10px] font-mono text-text-muted">• {r}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : null}

          {/* Confidence factor bar */}
          {confFactors && <ConfidenceFactorsBar factors={confFactors} labelMap={confLabelMap} />}

          {/* Blocked reason */}
          {signal.blocked_reason && (
            <p className="text-[9px] font-mono text-text-muted italic">
              ⊘ {signal.blocked_reason}
            </p>
          )}

          {/* Signal version history */}
          <SignalHistoryPanel signalId={signal.id} />
        </div>
      )}
    </div>
  );
}

export default function SignalsPage() {
  const {
    signalsMinConfidence, setSignalsMinConfidence,
    signalsPeriodLabel, signalsPeriodStart, signalsPeriodEnd, setSignalsPeriod,
    signalsStrategy, setSignalsStrategy,
    signalsHideInformational, setSignalsHideInformational,
  } = useStore();

  const period = periodFromLabel(signalsPeriodLabel, signalsPeriodStart, signalsPeriodEnd);
  const handlePeriodChange = (p: Period) => setSignalsPeriod(p.label, p.start, p.end);

  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);
  const [searchQuery, setSearchQuery] = useState("");

  useEffect(() => {
    setLoading(true);
    let cancelled = false;
    async function load() {
      try {
        const data = (await api.getSignals({
          generated_since: period.start.toISOString(),
          generated_until: period.end.toISOString(),
          limit: 200,
        })) as Signal[];
        if (!cancelled) setSignals(data);
      } catch {
        if (!cancelled) setSignals([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [signalsPeriodLabel, signalsPeriodStart, signalsPeriodEnd]);

  const strategies = Array.from(new Set(signals.map(s => s.strategy_name))).sort();

  const q = searchQuery.trim().toLowerCase();
  const displayed = signals.filter(s => {
    if (signalsHideInformational && !s.executable && s.blocked_reason === "Outside trade window") return false;
    if (signalsStrategy && s.strategy_name !== signalsStrategy) return false;
    if (signalsMinConfidence > 0 && (s.confidence == null || Number(s.confidence) < signalsMinConfidence)) return false;
    if (q && !s.symbol.toLowerCase().includes(q) && !s.reason.toLowerCase().includes(q) && !(s.ai_summary?.toLowerCase().includes(q))) return false;
    return true;
  });

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3 flex-wrap">
          <h1 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
            Signal History
          </h1>
          <PeriodFilter key={signalsPeriodLabel} value={period} onChange={handlePeriodChange} />
          {strategies.length > 1 && (
            <div className="flex items-center gap-1">
              <button
                onClick={() => setSignalsStrategy("")}
                className={`text-[10px] font-mono px-1.5 py-px rounded border transition-colors ${
                  signalsStrategy === ""
                    ? "bg-accent/20 text-accent border-accent/30"
                    : "text-text-muted border-border hover:text-text-primary"
                }`}
              >
                ALL
              </button>
              {strategies.map(s => (
                <button
                  key={s}
                  onClick={() => setSignalsStrategy(signalsStrategy === s ? "" : s)}
                  className={`text-[10px] font-mono px-1.5 py-px rounded border transition-colors ${
                    signalsStrategy === s
                      ? "bg-accent/20 text-accent border-accent/30"
                      : "text-text-muted border-border hover:text-text-primary"
                  }`}
                >
                  {STRATEGY_LABELS[s] ?? s}
                </button>
              ))}
            </div>
          )}
          <label className="flex items-center gap-1.5 text-[9px] font-mono text-text-muted">
            <span>CONF</span>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={signalsMinConfidence}
              onChange={(e) => setSignalsMinConfidence(Number(e.target.value))}
              className="w-16 h-1 accent-accent"
            />
            <span className={signalsMinConfidence > 0 ? "text-accent" : "text-text-muted"}>
              {signalsMinConfidence > 0 ? `${signalsMinConfidence}%` : "any"}
            </span>
          </label>
          <div className="relative flex items-center">
            <input
              type="text"
              value={searchQuery}
              onChange={e => setSearchQuery(e.target.value)}
              placeholder="search symbol / reason…"
              className="text-[10px] font-mono bg-bg-tertiary border border-border rounded px-2 py-px pr-5 text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/50 w-44"
            />
            {searchQuery && (
              <button
                onClick={() => setSearchQuery("")}
                className="absolute right-1 text-text-muted hover:text-text-primary text-[10px] font-mono leading-none"
              >
                ✕
              </button>
            )}
          </div>
        </div>
        <label className="flex items-center gap-1.5 cursor-pointer select-none">
          <input
            type="checkbox"
            checked={signalsHideInformational}
            onChange={e => setSignalsHideInformational(e.target.checked)}
            className="accent-accent w-3 h-3"
          />
          <span className="text-[10px] font-mono text-text-muted">Hide informational</span>
        </label>
      </div>

      <div className="rounded border border-border bg-bg-secondary overflow-hidden">
        {loading ? (
          <div className="px-3 py-6 text-center text-text-muted text-xs font-mono">loading...</div>
        ) : displayed.length === 0 ? (
          <div className="px-3 py-6 text-center text-text-muted text-xs font-mono">
            no signals generated yet
          </div>
        ) : (
          <div className="divide-y divide-border/30">
            {displayed.map(signal => (
              <SignalCard key={signal.id} signal={signal} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

"use client";

import { useState } from "react";
import { useStore } from "@/store";
import { formatINR } from "@/lib/formatters";
import { STRATEGY_LABELS } from "@/lib/constants";
import { api } from "@/lib/api";
import type { Signal } from "@/lib/types";
import { ExecuteSignalModal } from "./ExecuteSignalModal";

function formatSignalTime(isoString: string): string {
  try {
    const d = new Date(isoString);
    const time = d.toLocaleTimeString("en-IN", {
      timeZone: "Asia/Kolkata",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
    const date = d.toLocaleDateString("en-IN", {
      timeZone: "Asia/Kolkata",
      day: "2-digit",
      month: "short",
    });
    return `${time} · ${date}`;
  } catch {
    return isoString;
  }
}

export function ScannerPanel() {
  const { signals, updateSignal, removeSignal } = useStore();
  const pendingSignals = signals.filter((s) => s.status === "PENDING");

  const [execSignal, setExecSignal] = useState<Signal | null>(null);
  const [execError, setExecError] = useState<string | null>(null);

  const handleExecuted = (signalId: string) => {
    updateSignal(signalId, { status: "EXECUTED" });
    setExecSignal(null);
  };

  const handleReject = async (signalId: string) => {
    try {
      await api.rejectSignal(signalId);
      removeSignal(signalId);
    } catch (err) {
      console.error("Failed to reject signal:", err);
    }
  };

  return (
    <div className="rounded border border-border bg-bg-secondary">
      <div className="px-3 py-1.5 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
            Scanner
          </h2>
          {pendingSignals.length > 0 && (
            <span className="text-xs font-mono px-1 py-px rounded bg-accent/15 text-accent">
              {pendingSignals.length}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <div className="w-1 h-1 rounded-full bg-profit animate-pulse" />
          <span className="text-[10px] text-text-muted font-mono">LIVE</span>
        </div>
      </div>

      {execError && (
        <div className="px-3 py-1.5 bg-loss/10 border-b border-loss/20">
          <p className="text-xs font-mono text-loss">{execError}</p>
        </div>
      )}

      {pendingSignals.length === 0 ? (
        <div className="px-3 py-6 text-center">
          <p className="text-xs text-text-muted font-mono">waiting for signals...</p>
        </div>
      ) : (
        <div className="max-h-[400px] overflow-y-auto divide-y divide-border/40">
          {pendingSignals.map((signal) => (
            <SignalCard
              key={signal.id}
              signal={signal}
              onExec={() => setExecSignal(signal)}
              onDismiss={handleReject}
            />
          ))}
        </div>
      )}

      {execSignal && (
        <ExecuteSignalModal
          signal={execSignal}
          onClose={() => setExecSignal(null)}
          onExecuted={() => handleExecuted(execSignal.id)}
        />
      )}
    </div>
  );
}

const CONFIDENCE_FACTOR_LABELS: Record<string, string> = {
  bias_alignment:       "Bias",
  vwap_slope_alignment: "VWAP slope",
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
};

function SignalCard({
  signal,
  onExec,
  onDismiss,
}: {
  signal: Signal;
  onExec: () => void;
  onDismiss: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [aiExpanded, setAiExpanded] = useState(false);
  const [watchlistStatus, setWatchlistStatus] = useState<"idle" | "adding" | "done" | "error">("idle");

  const isFuture = signal.instrument_type === "FUTURE";
  const isCE = signal.signal_type === "BUY_CE";
  const isS5 = signal.strategy_name === "intraday_futures";
  const isBullish = isFuture ? signal.signal_type === "BUY_FUT" : isCE;
  const isUnresolved = !signal.executable && signal.instrument_type === "OPTION";

  const directionColor = isBullish ? "text-profit" : "text-loss";
  const directionArrow = isBullish ? "▲" : "▼";

  const symbolLabel = isFuture
    ? `${signal.symbol} FUT`
    : `${signal.symbol} ${signal.strike_price > 0 ? signal.strike_price : ""} ${isCE ? "CE" : "PE"}`;

  const risk = Math.abs(Number(signal.entry_price) - Number(signal.stop_loss));
  const reward = signal.target_price
    ? Math.abs(Number(signal.target_price) - Number(signal.entry_price))
    : 0;
  const rrRatio = risk > 0 && reward > 0 ? (reward / risk).toFixed(1) : null;

  // indicators from JSONB — guard against null (old signals or pre-Phase-2 rows)
  const ind = (signal.indicators ?? {}) as Record<string, number | string | null>;
  const vwap = typeof ind.vwap === "number" ? ind.vwap : null;
  const vwapDist =
    typeof ind.vwap_distance_pct === "number" ? ind.vwap_distance_pct : null;
  const oiConfirmed = ind.oi_confirmed != null ? Boolean(ind.oi_confirmed) : null;
  const indexEntry =
    signal.index_entry_price != null ? Number(signal.index_entry_price) : null;

  const lotsLabel =
    signal.lots != null && signal.lots > 0
      ? `${signal.lots}L`
      : null;
  const qtyLabel =
    signal.quantity != null && signal.quantity > 0
      ? `(${signal.quantity})`
      : null;

  const handleWatchlist = async (e: React.MouseEvent) => {
    e.stopPropagation();
    if (watchlistStatus !== "idle") return;
    setWatchlistStatus("adding");
    try {
      const fyers = signal.fyers_futures_symbol || signal.fyers_option_symbol;
      if (!fyers) {
        setWatchlistStatus("error");
        return;
      }
      const optionType = isCE ? "CE" : isFuture ? null : "PE";
      const segment = isFuture ? "FUT" : "OPT";
      await api.addToWatchlist({
        symbol: fyers,
        display: symbolLabel,
        segment,
        strike: signal.strike_price > 0 ? signal.strike_price : null,
        option_type: optionType,
        expiry: signal.expiry_date,
      });
      useStore.getState().addWatchlistItem({ symbol: fyers, display: symbolLabel, segment });
      setWatchlistStatus("done");
      setTimeout(() => setWatchlistStatus("idle"), 2000);
    } catch {
      setWatchlistStatus("error");
      setTimeout(() => setWatchlistStatus("idle"), 2500);
    }
  };

  return (
    <div className="px-3 py-2.5 hover:bg-bg-tertiary/30 transition-colors">
      {/* Row 1: Direction + Symbol + Time + Strategy + Confidence */}
      <div className="flex items-start justify-between gap-2 mb-1.5">
        <div className="flex items-center gap-1.5 min-w-0">
          <span className={`text-xs font-bold shrink-0 ${directionColor}`}>
            {directionArrow}
          </span>
          <span className="text-xs font-mono font-medium text-text-primary truncate">
            {symbolLabel}
          </span>
          <span className="text-[10px] font-mono text-text-muted shrink-0 hidden sm:inline">
            {formatSignalTime(signal.generated_at)}
          </span>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {signal.confidence != null && (
            <span
              className={`text-[10px] font-mono font-medium ${
                Number(signal.confidence) >= 70
                  ? "text-profit"
                  : Number(signal.confidence) >= 50
                  ? "text-accent"
                  : "text-loss"
              }`}
            >
              {Number(signal.confidence).toFixed(0)}%
            </span>
          )}
          <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-accent/10 text-accent">
            {STRATEGY_LABELS[signal.strategy_name] || signal.strategy_name}
          </span>
        </div>
      </div>

      {/* Row 2: Prices */}
      {!isUnresolved && (
        <div className="flex items-center gap-3 text-xs font-mono mb-1.5">
          <span className="text-text-secondary">
            ₹{Number(signal.entry_price).toLocaleString("en-IN")}
          </span>
          <span className="text-[10px] text-text-muted">SL</span>
          <span className="text-loss">
            ₹{Number(signal.stop_loss).toLocaleString("en-IN")}
          </span>
          {signal.target_price && (
            <>
              <span className="text-[10px] text-text-muted">T</span>
              <span className="text-profit">
                ₹{Number(signal.target_price).toLocaleString("en-IN")}
              </span>
            </>
          )}
          {rrRatio && (
            <span className="text-text-muted text-[10px]">R:R 1:{rrRatio}</span>
          )}
        </div>
      )}

      {/* Row 3: Context — strategy-aware */}
      {isS5 ? (
        <div className="space-y-1 mb-2">
          <div className="flex items-center gap-2 text-[10px] font-mono text-text-muted flex-wrap">
            {ind.setup_type && (
              <span className="px-1.5 py-px rounded bg-accent/15 text-accent">
                {String(ind.setup_type).replace("_", " ")}
              </span>
            )}
            {ind.enhanced_orb && (
              <span className="px-1 py-px rounded bg-profit/15 text-profit">Enhanced</span>
            )}
            {ind.phase && (
              <span className="text-text-secondary">{String(ind.phase).replace("_", " ")}</span>
            )}
            {typeof ind.rvol === "number" && (
              <span>
                RVOL{" "}
                <span className={Number(ind.rvol) >= 2.0 ? "text-profit" : Number(ind.rvol) >= 1.5 ? "text-accent" : "text-text-muted"}>
                  {Number(ind.rvol).toFixed(1)}x
                </span>
              </span>
            )}
            {vwap != null && (
              <span>VWAP <span className="text-text-secondary">{vwap.toLocaleString("en-IN")}</span></span>
            )}
            {typeof ind.orb_high === "number" && typeof ind.orb_low === "number" && (
              <span>ORB <span className="text-text-secondary">{Number(ind.orb_low).toLocaleString("en-IN")}–{Number(ind.orb_high).toLocaleString("en-IN")}</span></span>
            )}
            {typeof ind.pdh === "number" && (
              <span>PDH <span className="text-text-secondary">{Number(ind.pdh).toLocaleString("en-IN")}</span></span>
            )}
            {typeof ind.pdl === "number" && (
              <span>PDL <span className="text-text-secondary">{Number(ind.pdl).toLocaleString("en-IN")}</span></span>
            )}
            {typeof ind.gap_pct === "number" && (
              <span>Gap <span className={Number(ind.gap_pct) > 0 ? "text-profit" : "text-loss"}>{Number(ind.gap_pct) > 0 ? "+" : ""}{Number(ind.gap_pct).toFixed(1)}%</span></span>
            )}
          </div>
          {Array.isArray(ind.risk_warnings) && (ind.risk_warnings as string[]).length > 0 && (
            <div className="flex items-center gap-1.5 flex-wrap">
              {(ind.risk_warnings as string[]).map((w, i) => (
                <span key={i} className="text-[9px] font-mono px-1 py-px rounded bg-warning/10 text-warning">
                  ⚠ {w}
                </span>
              ))}
            </div>
          )}
        </div>
      ) : (
        <div className="flex items-center gap-3 text-[10px] font-mono text-text-muted mb-2">
          {indexEntry != null && (
            <span>
              Idx{" "}
              <span className="text-text-secondary">
                {indexEntry.toLocaleString("en-IN")}
              </span>
            </span>
          )}
          {vwap != null && (
            <span>
              VWAP{" "}
              <span className="text-text-secondary">
                {vwap.toLocaleString("en-IN")}
              </span>
              {vwapDist != null && (
                <span className="text-text-muted ml-0.5">
                  ({Math.abs(vwapDist).toFixed(3)}%)
                </span>
              )}
            </span>
          )}
          {oiConfirmed != null && (
            <span className={oiConfirmed ? "text-profit" : "text-text-muted"}>
              OI {oiConfirmed ? "✓" : "—"}
            </span>
          )}
        </div>
      )}

      {/* Row 4: Actions */}
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          onClick={(e) => {
            e.stopPropagation();
            onExec();
          }}
          className={`text-xs font-mono font-medium px-2 py-1 rounded transition-colors ${
            signal.executable
              ? "bg-profit/15 text-profit hover:bg-profit/25"
              : "bg-warning/10 text-warning border border-warning/30 hover:bg-warning/20"
          }`}
          title={signal.executable ? undefined : `Manual override — ${signal.blocked_reason || "not executable"}`}
        >
          EXEC
          {lotsLabel && (
            <span className={`ml-1 text-[10px] ${signal.executable ? "text-profit/70" : "text-warning/70"}`}>
              {lotsLabel}
              {qtyLabel && ` ${qtyLabel}`}
            </span>
          )}
        </button>
        {!signal.executable && signal.blocked_reason && (
          <span className="text-[9px] font-mono text-warning/70 italic">
            ⚠ {signal.blocked_reason}
          </span>
        )}

        <button
          onClick={handleWatchlist}
          title="Add to watchlist"
          className={`text-[10px] font-mono px-2 py-1 rounded border transition-colors ${
            watchlistStatus === "done"
              ? "border-profit/30 text-profit bg-profit/10"
              : watchlistStatus === "error"
              ? "border-loss/30 text-loss bg-loss/10"
              : "border-border/60 text-text-muted hover:text-accent hover:border-accent/40"
          }`}
        >
          {watchlistStatus === "adding"
            ? "..."
            : watchlistStatus === "done"
            ? "✓ Added"
            : watchlistStatus === "error"
            ? "Failed"
            : "+ Watch"}
        </button>

        <div className="ml-auto flex items-center gap-1">
          <button
            onClick={() => { setExpanded(!expanded); if (aiExpanded) setAiExpanded(false); }}
            className="text-[10px] font-mono text-text-muted hover:text-text-secondary px-1.5 py-1 rounded transition-colors"
          >
            {expanded ? "▲ Less" : "▼ Details"}
          </button>

          <button
            onClick={() => { setAiExpanded(!aiExpanded); if (expanded) setExpanded(false); }}
            className={`text-[10px] font-mono px-1.5 py-1 rounded transition-colors ${
              signal.ai_summary
                ? "text-accent hover:text-accent/80 hover:bg-accent/10"
                : "text-text-muted hover:text-text-secondary"
            }`}
            title={signal.ai_summary ? "AI analysis available" : "No AI analysis yet"}
          >
            {signal.ai_summary ? "✦ AI" : "AI"}
          </button>
        </div>

        <button
          onClick={(e) => {
            e.stopPropagation();
            onDismiss(signal.id);
          }}
          className="text-[10px] font-mono px-1.5 py-1 rounded text-text-muted hover:text-loss hover:bg-loss/10 transition-colors"
        >
          ✕
        </button>
      </div>

      {/* Expanded: raw reason */}
      {expanded && signal.reason && (
        <div className="mt-2 pt-2 border-t border-border/30 animate-fade-in">
          <p className="text-[10px] text-text-secondary leading-relaxed font-mono">
            {signal.reason}
          </p>
        </div>
      )}

      {/* AI Notes panel */}
      {aiExpanded && (
        <div className="mt-2 pt-2 border-t border-accent/20 animate-fade-in space-y-2">
          {signal.ai_summary ? (
            <>
              {/* Summary + adjustment */}
              <div className="flex items-start gap-2">
                <span className="text-accent text-[10px] shrink-0 mt-px">✦</span>
                <p className="text-[10px] font-mono text-text-primary leading-relaxed flex-1">
                  {signal.ai_summary}
                </p>
                {signal.ai_adjustment != null && signal.ai_adjustment !== 0 && (
                  <span className={`text-[9px] font-mono px-1 py-px rounded shrink-0 ${
                    signal.ai_adjustment > 0
                      ? "bg-profit/10 text-profit"
                      : "bg-loss/10 text-loss"
                  }`}>
                    AI {signal.ai_adjustment > 0 ? "+" : ""}{signal.ai_adjustment}
                  </span>
                )}
              </div>

              {/* Rationale */}
              {signal.ai_rationale && (
                <p className="text-[10px] font-mono text-text-secondary leading-relaxed pl-4">
                  {signal.ai_rationale}
                </p>
              )}

              {/* Supports + Risks */}
              {(() => {
                const supports = (ind.ai_key_supports ?? []) as string[];
                const risks = (ind.ai_key_risks ?? []) as string[];
                return (supports.length > 0 || risks.length > 0) ? (
                  <div className="grid grid-cols-2 gap-2 pl-4">
                    {supports.length > 0 && (
                      <div>
                        <p className="text-[9px] font-mono text-profit/70 uppercase tracking-wider mb-1">Supports</p>
                        <ul className="space-y-0.5">
                          {supports.map((s, i) => (
                            <li key={i} className="text-[9px] font-mono text-text-secondary flex gap-1">
                              <span className="text-profit shrink-0">+</span>
                              <span>{s}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                    {risks.length > 0 && (
                      <div>
                        <p className="text-[9px] font-mono text-loss/70 uppercase tracking-wider mb-1">Risks</p>
                        <ul className="space-y-0.5">
                          {risks.map((r, i) => (
                            <li key={i} className="text-[9px] font-mono text-text-secondary flex gap-1">
                              <span className="text-loss shrink-0">−</span>
                              <span>{r}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </div>
                ) : null;
              })()}

              {/* Confidence factor bars */}
              {(() => {
                const factors = ind.confidence_factors as unknown as Record<string, number> | undefined;
                const factorLabels = isS5 ? S5_CONFIDENCE_FACTOR_LABELS : CONFIDENCE_FACTOR_LABELS;
                return factors ? (
                  <div className="pl-4">
                    <p className="text-[9px] font-mono text-text-muted uppercase tracking-wider mb-1.5">
                      Confidence factors
                    </p>
                    <div className="space-y-0.5">
                      {Object.entries(factorLabels).map(([key, label]) => {
                        const val = factors[key];
                        if (val == null) return null;
                        const pct = Math.round(val * 100);
                        const color = pct >= 70 ? "bg-profit" : pct >= 40 ? "bg-accent" : "bg-loss";
                        return (
                          <div key={key} className="flex items-center gap-2">
                            <span className="text-[9px] font-mono text-text-muted w-20 shrink-0">{label}</span>
                            <div className="flex-1 h-1 bg-border/40 rounded-full overflow-hidden">
                              <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
                            </div>
                            <span className="text-[9px] font-mono text-text-muted w-7 text-right shrink-0">{pct}%</span>
                          </div>
                        );
                      })}
                    </div>
                  </div>
                ) : null;
              })()}
            </>
          ) : (
            <p className="text-[10px] font-mono text-text-muted pl-4 italic">
              No AI analysis yet — fires on next signal for this symbol.
            </p>
          )}
        </div>
      )}
    </div>
  );
}

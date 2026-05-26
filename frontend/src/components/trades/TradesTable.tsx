"use client";

import { useState } from "react";
import { formatINR, formatPercent, formatTime, formatDate, pnlColor } from "@/lib/formatters";
import { STRATEGY_LABELS, STATUS_COLORS } from "@/lib/constants";
import { SignalHistoryPanel } from "@/components/signals/SignalHistoryPanel";
import type { Trade } from "@/lib/types";

export type HoldData = Map<string, {
  exitPrice: number;
  pnl: number;
  netPnl: number | null;
  pnlPercent: number;
  chargesJson: { brokerage: number; stt: number; exchange_txn: number; gst: number; sebi_charges: number; stamp_duty: number; total: number } | null;
  exitTime: string | null;
  outcome: string | null;
}>;

interface Props {
  trades: Trade[];
  loading: boolean;
  showSource?: boolean;
  showSignalData?: boolean;
  simLots?: number | null;
  showNetPnL?: boolean;
  holdData?: HoldData;
  holdScenario?: string;
}

function confidenceColor(conf: number | null): string {
  if (conf == null) return "text-text-muted";
  if (conf >= 80) return "text-profit";
  if (conf >= 70) return "text-accent";
  if (conf >= 50) return "text-text-secondary";
  return "text-loss";
}

const VWAP_CONFIDENCE_FACTOR_LABELS: Record<string, string> = {
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
  oi_factor:    "OI",
};

function aiActionBadge(action: string) {
  const cls =
    action === "PROCEED"
      ? "bg-profit/10 text-profit"
      : action === "SKIP"
      ? "bg-loss/10 text-loss"
      : "bg-warning/10 text-warning";
  return (
    <span className={`text-[9px] font-mono px-1 py-px rounded ${cls}`}>
      {action}
    </span>
  );
}

function ConfidenceFactorsBar({ factors, labelMap }: { factors: Record<string, number>; labelMap: Record<string, string> }) {
  const keys = Object.keys(labelMap).filter(k => factors[k] !== undefined);
  if (keys.length === 0) return null;
  return (
    <div>
      <p className="text-[9px] font-mono text-text-muted uppercase tracking-wider mb-1">Confidence Factors</p>
      <div className="flex flex-col gap-0.5">
        {keys.map(k => {
          const val = factors[k] as number;
          const pct = Math.round(val * 100);
          const color = val >= 0.7 ? "bg-profit/60" : val >= 0.4 ? "bg-accent/60" : "bg-loss/60";
          return (
            <div key={k} className="flex items-center gap-2">
              <span className="text-[9px] font-mono text-text-muted w-14 shrink-0">{labelMap[k]}</span>
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

function TradeDetailsPanel({ trade }: { trade: Trade }) {
  const snap = trade.signal_snapshot;

  if (!snap) {
    return (
      <div className="px-4 py-3 text-xs font-mono text-text-muted">
        No signal snapshot available
        {trade.signal_id && (
          <div className="mt-2">
            <SignalHistoryPanel signalId={trade.signal_id} />
          </div>
        )}
      </div>
    );
  }

  const indicators = (snap.indicators ?? {}) as Record<string, unknown>;
  const confidenceFactors = indicators.confidence_factors as Record<string, number> | undefined;
  const aiKeySupports = indicators.ai_key_supports as string[] | undefined;
  const aiKeyRisks = indicators.ai_key_risks as string[] | undefined;
  const isS5 = trade.strategy_name === "intraday_futures";
  const factorLabels = isS5 ? S5_CONFIDENCE_FACTOR_LABELS : VWAP_CONFIDENCE_FACTOR_LABELS;

  return (
    <div className="px-4 py-3 space-y-3">
      <div className="grid grid-cols-2 gap-4">
        {/* Left column: AI analysis */}
        <div className="space-y-2">
          {snap.ai_action && (
            <div className="flex items-center gap-2">
              {aiActionBadge(snap.ai_action)}
              {snap.ai_adjustment != null && snap.ai_adjustment !== 0 && (
                <span className={`text-[10px] font-mono px-1 py-px rounded ${
                  snap.ai_adjustment > 0 ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss"
                }`}>
                  {snap.ai_adjustment > 0 ? "+" : ""}{snap.ai_adjustment}
                </span>
              )}
            </div>
          )}

          {snap.ai_summary && (
            <p className="text-[11px] font-mono text-text-secondary leading-relaxed">
              <span className="text-accent">✦</span> {snap.ai_summary}
            </p>
          )}

          {snap.ai_rationale && (
            <p className="text-[10px] font-mono text-text-muted leading-relaxed">{snap.ai_rationale}</p>
          )}

          {snap.reason && (
            <div>
              <p className="text-[9px] font-mono text-text-muted uppercase tracking-wider mb-0.5">Signal Reason</p>
              <p className="text-[10px] font-mono text-text-secondary">{snap.reason}</p>
            </div>
          )}
        </div>

        {/* Right column: factors + supports/risks */}
        <div className="space-y-3">
          {confidenceFactors && (
            <ConfidenceFactorsBar factors={confidenceFactors} labelMap={factorLabels} />
          )}

          {(aiKeySupports?.length || aiKeyRisks?.length) ? (
            <div className="grid grid-cols-2 gap-3">
              {aiKeySupports && aiKeySupports.length > 0 && (
                <div>
                  <p className="text-[9px] font-mono text-profit/70 uppercase tracking-wider mb-0.5">Supports</p>
                  <ul className="space-y-0.5">
                    {aiKeySupports.map((s, i) => (
                      <li key={i} className="text-[10px] font-mono text-text-muted">+ {s}</li>
                    ))}
                  </ul>
                </div>
              )}
              {aiKeyRisks && aiKeyRisks.length > 0 && (
                <div>
                  <p className="text-[9px] font-mono text-loss/70 uppercase tracking-wider mb-0.5">Risks</p>
                  <ul className="space-y-0.5">
                    {aiKeyRisks.map((r, i) => (
                      <li key={i} className="text-[10px] font-mono text-text-muted">- {r}</li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          ) : null}
        </div>
      </div>

      {trade.signal_id && (
        <SignalHistoryPanel signalId={trade.signal_id} />
      )}
    </div>
  );
}

function formatLatency(entryTime: string, generatedAt?: string): string | null {
  if (!generatedAt) return null;
  const diffMs = new Date(entryTime).getTime() - new Date(generatedAt).getTime();
  if (diffMs < 0 || isNaN(diffMs)) return null;
  const secs = Math.round(diffMs / 1000);
  if (secs < 60) return `${secs}s`;
  const mins = Math.floor(secs / 60);
  const remSecs = secs % 60;
  if (mins < 60) return remSecs > 0 ? `${mins}m ${remSecs}s` : `${mins}m`;
  const hrs = Math.floor(mins / 60);
  const remMins = mins % 60;
  return remMins > 0 ? `${hrs}h ${remMins}m` : `${hrs}h`;
}

function TradeRow({
  trade,
  showSource,
  showSignalData,
  showNetPnL,
  colCount,
  holdData,
  holdScenario,
}: {
  trade: Trade;
  showSource: boolean;
  showSignalData: boolean;
  showNetPnL: boolean;
  colCount: number;
  holdData?: HoldData;
  holdScenario?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = trade.signal_snapshot || trade.signal_id;
  const latency = formatLatency(trade.entry_time, trade.signal_snapshot?.generated_at);

  return (
    <>
      <tr className="border-t border-border/30 hover:bg-bg-tertiary/30 hover:relative hover:z-10">
        <td className="px-3 py-1.5 text-text-muted text-xs font-mono whitespace-nowrap">
          {formatDate(trade.entry_time)}
          <br />
          {formatTime(trade.entry_time)}
          {trade.exit_time && (
            <span className="text-text-muted/60"> → {formatTime(trade.exit_time)}</span>
          )}
          {latency && (
            <>
              <br />
              <span className="text-[9px] text-accent/70">fill {latency}</span>
            </>
          )}
        </td>
        <td className="px-3 py-1.5 font-mono">
          <span className="font-medium">{trade.symbol}</span>
          {(trade.is_permanent_watchlist || trade.signal_is_permanent_watchlist) && (
            <span className="ml-1 text-[9px] font-mono px-1 py-px rounded border border-accent/40 text-accent/70">P</span>
          )}
          {trade.option_type && (
            <span
              className={`ml-1 text-[10px] ${
                trade.option_type === "CE" ? "text-profit" : "text-loss"
              }`}
            >
              {trade.strike_price} {trade.option_type}
            </span>
          )}
        </td>
        <td className="px-3 py-1.5 text-xs font-mono text-text-secondary">{trade.side}</td>
        <td className="px-3 py-1.5 text-xs font-mono text-text-secondary">
          {trade.lots}L
          <span className="text-text-muted/60 ml-0.5">({trade.quantity})</span>
        </td>
        <td className="px-3 py-1.5 text-right font-mono">{formatINR(trade.entry_price)}</td>
        <td className="px-3 py-1.5 text-right font-mono">
          {trade.exit_price ? formatINR(trade.exit_price) : "—"}
        </td>
        <td className="px-3 py-1.5 text-right font-mono text-xs text-text-muted whitespace-nowrap">
          <span className="text-loss/70">{formatINR(trade.stop_loss)}</span>
          <br />
          <span className="text-profit/70">{trade.target_price != null ? formatINR(trade.target_price) : "—"}</span>
        </td>
        <td className={`px-3 py-1.5 text-right font-mono font-medium whitespace-nowrap ${pnlColor(
          showNetPnL && trade.net_pnl != null ? trade.net_pnl : (trade.pnl ?? 0)
        )}`}>
          {trade.pnl != null ? (
            <div className="flex items-center justify-end gap-0.5">
              <div>
                {formatINR(showNetPnL && trade.net_pnl != null ? trade.net_pnl : trade.pnl)}
                <div className="text-[10px]">{formatPercent(trade.pnl_percent ?? 0)}</div>
              </div>
              {showNetPnL && trade.charges_json && (
                <div className="relative group inline-block ml-1 cursor-help text-text-muted/40 text-[9px]">
                  i
                  <div className="absolute top-full right-0 z-50 hidden group-hover:block
                                  bg-bg-elevated border border-border rounded p-2 text-[9px]
                                  font-mono w-44 shadow-lg whitespace-nowrap text-text-secondary font-normal text-left mt-1">
                    <div>Brokerage: {formatINR(trade.charges_json.brokerage)}</div>
                    <div>STT: {formatINR(trade.charges_json.stt)}</div>
                    <div>Exchange: {formatINR(trade.charges_json.exchange_txn)}</div>
                    <div>GST: {formatINR(trade.charges_json.gst)}</div>
                    <div>SEBI: {formatINR(trade.charges_json.sebi_charges)}</div>
                    <div>Stamp: {formatINR(trade.charges_json.stamp_duty)}</div>
                    <div className="border-t border-border/40 mt-1 pt-1 text-text-primary">
                      Total: {formatINR(trade.charges_json.total)}
                    </div>
                  </div>
                </div>
              )}
            </div>
          ) : (
            "—"
          )}
        </td>
        {holdData && (() => {
          const h = holdData.get(trade.id);
          const holdDisplayPnl = h ? (showNetPnL && h.netPnl != null ? h.netPnl : h.pnl) : 0;
          return (
            <>
              <td className="px-3 py-1.5 text-right font-mono">
                {h ? formatINR(h.exitPrice) : "—"}
              </td>
              <td className={`px-3 py-1.5 text-right font-mono font-medium whitespace-nowrap ${h ? pnlColor(holdDisplayPnl) : ""}`}>
                {h ? (
                  <div className="flex items-center justify-end gap-0.5">
                    <div>
                      {formatINR(holdDisplayPnl)}
                      <div className="text-[10px]">{formatPercent(h.pnlPercent)}</div>
                    </div>
                    {showNetPnL && h.chargesJson && (
                      <div className="relative group inline-block ml-1 cursor-help text-text-muted/40 text-[9px]">
                        i
                        <div className="absolute top-full right-0 z-50 hidden group-hover:block
                                        bg-bg-elevated border border-border rounded p-2 text-[9px]
                                        font-mono w-44 shadow-lg whitespace-nowrap text-text-secondary font-normal text-left mt-1">
                          <div>Brokerage: {formatINR(h.chargesJson.brokerage)}</div>
                          <div>STT: {formatINR(h.chargesJson.stt)}</div>
                          <div>Exchange: {formatINR(h.chargesJson.exchange_txn)}</div>
                          <div>GST: {formatINR(h.chargesJson.gst)}</div>
                          <div>SEBI: {formatINR(h.chargesJson.sebi_charges)}</div>
                          <div>Stamp: {formatINR(h.chargesJson.stamp_duty)}</div>
                          <div className="border-t border-border/40 mt-1 pt-1 text-text-primary">
                            Total: {formatINR(h.chargesJson.total)}
                          </div>
                        </div>
                      </div>
                    )}
                  </div>
                ) : "—"}
              </td>
              <td className="px-3 py-1.5 text-right font-mono whitespace-nowrap">
                <span className="text-accent/70">{h?.exitTime ? formatTime(h.exitTime) : "—"}</span>
                <span className="text-text-muted/60"> / </span>
                <span className="text-text-muted">{trade.exit_time ? formatTime(trade.exit_time) : "—"}</span>
              </td>
              {holdScenario === "sl_tgt" && (
                <td className="px-3 py-1.5 text-center font-mono text-[10px] font-bold">
                  {h?.outcome === "TGT" ? (
                    <span className="text-green-400">TGT</span>
                  ) : h?.outcome === "SL" ? (
                    <span className="text-red-400">SL</span>
                  ) : (
                    <span className="text-text-muted">—</span>
                  )}
                </td>
              )}
            </>
          );
        })()}
        <td className="px-3 py-1.5">
          <span className="text-[10px] font-mono px-1 py-px rounded bg-accent/10 text-accent">
            {STRATEGY_LABELS[trade.strategy_name] || trade.strategy_name}
          </span>
        </td>
        {showSignalData && (
          <td className={`px-3 py-1.5 text-right font-mono text-xs ${confidenceColor(trade.signal_confidence)}`}>
            {trade.signal_confidence != null ? Math.round(Number(trade.signal_confidence)) : "—"}
          </td>
        )}
        {showSignalData && (
          <td className="px-3 py-1.5">
            {trade.signal_ai_action ? (
              aiActionBadge(trade.signal_ai_action)
            ) : (
              <span className="text-text-muted text-xs font-mono">—</span>
            )}
          </td>
        )}
        <td className="px-3 py-1.5 text-xs font-mono text-text-muted">
          {trade.exit_reason || "—"}
        </td>
        <td className="px-3 py-1.5 text-right text-xs font-mono text-text-secondary whitespace-nowrap">
          {trade.margin_required != null ? formatINR(trade.margin_required) : "—"}
        </td>
        <td className="px-3 py-1.5">
          <span
            className={`text-[10px] font-mono px-1 py-px rounded ${
              STATUS_COLORS[trade.status] || ""
            }`}
          >
            {trade.status}
          </span>
        </td>
        {showSource && (
          <td className="px-3 py-1.5">
            <span className="text-[9px] font-mono px-1 py-px rounded bg-purple-500/15 text-purple-400">
              {trade.source}
            </span>
          </td>
        )}
        <td className="px-1 py-1.5">
          {hasDetails && (
            <button
              onClick={() => setExpanded(!expanded)}
              className="text-[10px] font-mono text-text-muted hover:text-accent transition-colors"
            >
              {expanded ? "▲" : "▼"}
            </button>
          )}
        </td>
      </tr>
      {expanded && hasDetails && (
        <tr className="bg-bg-tertiary/20">
          <td colSpan={colCount} className="p-0">
            <div className="animate-fade-in">
              <TradeDetailsPanel trade={trade} />
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

export function TradesTable({ trades, loading, showSource = false, showSignalData = false, simLots = null, showNetPnL = false, holdData, holdScenario }: Props) {
  if (loading) {
    return (
      <div className="px-3 py-6 text-center text-text-muted text-xs font-mono">loading...</div>
    );
  }

  if (trades.length === 0) {
    return (
      <div className="px-3 py-6 text-center text-text-muted text-xs font-mono">
        no trades in this period
      </div>
    );
  }

  // base columns: Date, Symbol, Type, Lots, Entry, SL/Tgt, Exit, P&L, Strategy, Exit Reason, Margin, Status, Details toggle
  let colCount = 13;
  if (showSignalData) colCount += 2;
  if (showSource) colCount += 1;
  if (holdData && holdData.size > 0) colCount += holdScenario === "sl_tgt" ? 4 : 3;

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="text-[10px] text-text-muted uppercase font-mono tracking-wider bg-bg-tertiary/40">
            <th className="text-left px-3 py-1.5">Date / Time</th>
            <th className="text-left px-3 py-1.5">Symbol</th>
            <th className="text-left px-3 py-1.5">Type</th>
            <th className="text-left px-3 py-1.5">Lots</th>
            <th className="text-right px-3 py-1.5">Entry</th>
            <th className="text-right px-3 py-1.5">Exit</th>
            <th className="text-right px-3 py-1.5">SL / Tgt</th>
            <th className="text-right px-3 py-1.5">
              P&amp;L
              {showNetPnL && <span className="normal-case font-normal text-accent/60 ml-1">net</span>}
              {simLots != null && (
                <span className="normal-case font-normal text-accent/60 ml-1">sim {simLots}L</span>
              )}
            </th>
            {holdData && holdData.size > 0 && <th className="text-right px-3 py-1.5 text-accent/70">Hold Exit</th>}
            {holdData && holdData.size > 0 && (
              <th className="text-right px-3 py-1.5 text-accent/70">
                Hold P&amp;L
                {showNetPnL && <span className="normal-case font-normal text-accent/60 ml-1">net</span>}
              </th>
            )}
            {holdData && holdData.size > 0 && <th className="text-right px-3 py-1.5"><span className="text-accent/70">Hold Exit Time</span> / Exit Time</th>}
            {holdData && holdData.size > 0 && holdScenario === "sl_tgt" && <th className="text-center px-3 py-1.5 text-accent/70">Outcome</th>}
            <th className="text-left px-3 py-1.5">Strategy</th>
            {showSignalData && <th className="text-right px-3 py-1.5">Conf</th>}
            {showSignalData && <th className="text-left px-3 py-1.5">AI</th>}
            <th className="text-left px-3 py-1.5">Exit Reason</th>
            <th className="text-right px-3 py-1.5">Margin</th>
            <th className="text-left px-3 py-1.5">Status</th>
            {showSource && <th className="text-left px-3 py-1.5">Source</th>}
            <th className="w-6"></th>
          </tr>
        </thead>
        <tbody>
          {trades.map((trade) => (
            <TradeRow
              key={trade.id}
              trade={trade}
              showSource={showSource}
              showSignalData={showSignalData}
              showNetPnL={showNetPnL}
              colCount={colCount}
              holdData={holdData && holdData.size > 0 ? holdData : undefined}
              holdScenario={holdScenario}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}

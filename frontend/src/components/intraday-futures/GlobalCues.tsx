"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { S5GlobalCues, S5MorningBriefing } from "@/lib/types";

function CueItem({ label, value, suffix = "%", absValue, absDecimals = 2 }: {
  label: string;
  value?: number;
  suffix?: string;
  absValue?: number;
  absDecimals?: number;
}) {
  if (value == null) return null;
  const color = value > 0 ? "text-profit" : value < 0 ? "text-loss" : "text-text-muted";
  return (
    <div className="flex items-center justify-between py-0.5">
      <span className="text-[10px] font-mono text-text-muted uppercase">{label}</span>
      <div className="flex items-center gap-2">
        {absValue != null && (
          <span className="text-[10px] font-mono text-text-muted">{absValue.toFixed(absDecimals)}</span>
        )}
        <span className={`text-xs font-mono ${color}`}>
          {value > 0 ? "+" : ""}{value.toFixed(2)}{suffix}
        </span>
      </div>
    </div>
  );
}

function BiasBar({ score }: { score: number }) {
  const pct = Math.round(((score + 1) / 2) * 100);
  const color = score > 0.15 ? "bg-profit" : score < -0.15 ? "bg-loss" : "bg-text-muted";
  return (
    <div className="flex items-center gap-1.5">
      <div className="flex-1 h-1 bg-bg-tertiary rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-[10px] font-mono text-text-muted w-8 text-right">
        {score > 0 ? "+" : ""}{score.toFixed(2)}
      </span>
    </div>
  );
}

export function GlobalCues({ date, refreshKey }: { date: string | null; refreshKey?: number }) {
  const [cues, setCues] = useState<S5GlobalCues>({});
  const [briefing, setBriefing] = useState<S5MorningBriefing | null>(null);
  const [expanded, setExpanded] = useState(true);
  const [localRefreshKey, setLocalRefreshKey] = useState(0);
  const [refreshing, setRefreshing] = useState(false);

  const isManualRefresh = localRefreshKey > 0;

  useEffect(() => {
    setRefreshing(true);
    Promise.all([
      api.getIntradayFuturesGlobalCues(date ?? undefined, isManualRefresh).then(setCues).catch(() => {}),
      api.getIntradayFuturesBriefing(date ?? undefined).then(setBriefing).catch(() => {}),
    ]).finally(() => setRefreshing(false));
  }, [date, refreshKey, localRefreshKey]);

  const handleRefresh = (e: React.MouseEvent) => {
    e.stopPropagation();
    setLocalRefreshKey((k) => k + 1);
  };

  const hasCues = cues.us_vix != null || cues.nifty_pct != null;
  const hasBriefing = briefing && (briefing.approach || briefing.summary);

  const biasColor =
    cues.overnight_bias === "BULLISH" ? "bg-profit/20 text-profit" :
    cues.overnight_bias === "BEARISH" ? "bg-loss/20 text-loss" :
    "bg-bg-tertiary text-text-muted";

  return (
    <div className="border border-border rounded bg-bg-secondary">
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-border">
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex-1 text-left hover:opacity-80"
        >
          <span className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
            Global Cues & Briefing
          </span>
        </button>
        <div className="flex items-center gap-2">
          <button
            onClick={handleRefresh}
            disabled={refreshing}
            className={`text-xs font-mono px-1.5 py-0.5 rounded border border-border text-text-muted hover:text-text-secondary hover:border-text-muted transition-colors ${refreshing ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
            title="Fetch latest from yfinance"
          >
            {refreshing ? "…" : "↻ refresh"}
          </button>
          <button
            onClick={() => setExpanded(!expanded)}
            className="text-text-muted text-xs hover:text-text-secondary"
          >
            {expanded ? "▾" : "▸"}
          </button>
        </div>
      </div>

      {expanded && (
        <div className="px-3 py-2 space-y-3">
          {/* Morning Briefing */}
          {hasBriefing && (
            <div className="space-y-1.5">
              <span className="text-[10px] font-mono text-accent uppercase tracking-wider">Briefing</span>
              {briefing!.approach && (
                <div className="flex items-center gap-2">
                  <span className="text-[10px] font-mono text-text-muted">Approach:</span>
                  <span className={`text-[10px] font-mono px-1 py-px rounded ${
                    briefing!.approach === "AGGRESSIVE" ? "bg-profit/20 text-profit" :
                    briefing!.approach === "CONSERVATIVE" ? "bg-loss/20 text-loss" :
                    "bg-accent/20 text-accent"
                  }`}>
                    {briefing!.approach}
                  </span>
                </div>
              )}
              {briefing!.sector_bias && (
                <div className="flex items-center gap-1 flex-wrap">
                  <span className="text-[10px] font-mono text-text-muted">Sectors:</span>
                  {(Array.isArray(briefing!.sector_bias) ? briefing!.sector_bias : [briefing!.sector_bias]).map((s) => (
                    <span key={s} className="text-[10px] font-mono px-1 py-px rounded bg-bg-tertiary text-text-secondary">
                      {s}
                    </span>
                  ))}
                </div>
              )}
              {briefing!.summary && (
                <p className="text-xs font-mono text-text-secondary leading-relaxed">
                  {briefing!.summary}
                </p>
              )}
              {briefing!.flags && briefing!.flags.length > 0 && (
                <div className="flex gap-1 flex-wrap">
                  {briefing!.flags.map((f, i) => (
                    <span key={i} className="text-[10px] font-mono px-1 py-px rounded bg-warning/20 text-warning">
                      {f}
                    </span>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Divider */}
          {hasBriefing && hasCues && <div className="border-t border-border/50" />}

          {/* Global Market Cues */}
          {hasCues ? (
            <div className="space-y-1.5">
              {/* Header row: label + overnight bias badge + pre-open tag */}
              <div className="flex items-center gap-2">
                <span className="text-[10px] font-mono text-accent uppercase tracking-wider">Markets</span>
                {cues.overnight_bias && (
                  <span className={`text-[10px] font-mono px-1 py-px rounded ${biasColor}`}>
                    {cues.overnight_bias}
                  </span>
                )}
                {cues.preopen_reassessed && (
                  <span className="text-[10px] font-mono px-1 py-px rounded bg-accent/10 text-accent">
                    pre-open ✓
                  </span>
                )}
              </div>

              {/* Global score bar */}
              {cues.global_score != null && (
                <BiasBar score={cues.global_score} />
              )}

              <div className="space-y-0.5 pt-0.5">
                {/* India-specific */}
                <CueItem label="Nifty Gap" value={cues.nifty_gap_pct} />
                <CueItem label="Nifty" value={cues.nifty_pct} absValue={cues.nifty_price} absDecimals={0} />

                {/* US markets */}
                <CueItem label="S&P 500" value={cues.sp500_close_pct} absValue={cues.sp500_price} absDecimals={0} />
                <CueItem label="Nasdaq" value={cues.nasdaq_close_pct} absValue={cues.nasdaq_price} absDecimals={0} />
                <CueItem label="Dow Futures" value={cues.dow_futures_pct} absValue={cues.dow_futures_price} absDecimals={0} />

                {/* Commodities & FX */}
                <CueItem label="Crude" value={cues.crude_pct} absValue={cues.crude_price} absDecimals={2} />
                <CueItem label="USD/INR" value={cues.usdinr_pct} absValue={cues.usdinr_price} absDecimals={2} />
                <CueItem label="DXY" value={cues.dxy_pct} absValue={cues.dxy_price} absDecimals={2} />

                {/* Volatility */}
                {cues.india_vix_live != null && (
                  <div className="flex items-center justify-between py-0.5">
                    <span className="text-[10px] font-mono text-text-muted uppercase">India VIX</span>
                    <span className={`text-xs font-mono ${cues.india_vix_live > 20 ? "text-loss" : cues.india_vix_live > 15 ? "text-warning" : "text-text-secondary"}`}>
                      {cues.india_vix_live.toFixed(2)}
                    </span>
                  </div>
                )}
                {cues.us_vix != null && (
                  <div className="flex items-center justify-between py-0.5">
                    <span className="text-[10px] font-mono text-text-muted uppercase">US VIX</span>
                    <span className={`text-xs font-mono ${cues.us_vix > 20 ? "text-loss" : "text-text-secondary"}`}>
                      {cues.us_vix.toFixed(2)}
                    </span>
                  </div>
                )}
              </div>

              {cues.flags && cues.flags.length > 0 && (
                <div className="flex gap-1 flex-wrap mt-1">
                  {cues.flags.map((f, i) => (
                    <span key={i} className="text-[10px] font-mono px-1 py-px rounded bg-warning/20 text-warning">
                      {f}
                    </span>
                  ))}
                </div>
              )}
            </div>
          ) : !hasBriefing ? (
            <div className="text-center text-text-muted text-xs font-mono py-2">
              no data — run briefing
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}

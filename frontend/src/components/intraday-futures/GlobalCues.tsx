"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { S5GlobalCues, S5MorningBriefing } from "@/lib/types";

function CueItem({ label, value, suffix = "%" }: { label: string; value?: number; suffix?: string }) {
  if (value == null) return null;
  const color = value > 0 ? "text-profit" : value < 0 ? "text-loss" : "text-text-muted";
  return (
    <div className="flex items-center justify-between py-0.5">
      <span className="text-[10px] font-mono text-text-muted uppercase">{label}</span>
      <span className={`text-xs font-mono ${color}`}>
        {value > 0 ? "+" : ""}{value.toFixed(2)}{suffix}
      </span>
    </div>
  );
}

export function GlobalCues({ date }: { date: string | null }) {
  const [cues, setCues] = useState<S5GlobalCues>({});
  const [briefing, setBriefing] = useState<S5MorningBriefing | null>(null);
  const [expanded, setExpanded] = useState(true);

  useEffect(() => {
    api.getIntradayFuturesGlobalCues(date ?? undefined).then(setCues).catch(() => {});
    api.getIntradayFuturesBriefing(date ?? undefined).then(setBriefing).catch(() => {});
  }, [date]);

  const hasCues = cues.us_vix != null || cues.nifty_pct != null;
  const hasBriefing = briefing && (briefing.approach || briefing.summary);

  return (
    <div className="border border-border rounded bg-bg-secondary">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-3 py-1.5 border-b border-border hover:bg-bg-tertiary"
      >
        <span className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          Global Cues & Briefing
        </span>
        <span className="text-text-muted text-xs">{expanded ? "▾" : "▸"}</span>
      </button>

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
            <div className="space-y-0.5">
              <span className="text-[10px] font-mono text-accent uppercase tracking-wider">Markets</span>
              <CueItem label="Nifty" value={cues.nifty_pct} />
              <CueItem label="S&P 500" value={cues.sp500_close_pct} />
              <CueItem label="Nasdaq" value={cues.nasdaq_close_pct} />
              <CueItem label="Dow Futures" value={cues.dow_futures_pct} />
              <CueItem label="Crude" value={cues.crude_pct} />
              <CueItem label="USD/INR" value={cues.usdinr_pct} />
              <CueItem label="DXY" value={cues.dxy_pct} />
              {cues.us_vix != null && (
                <div className="flex items-center justify-between py-0.5">
                  <span className="text-[10px] font-mono text-text-muted uppercase">US VIX</span>
                  <span className={`text-xs font-mono ${cues.us_vix > 20 ? "text-loss" : "text-text-secondary"}`}>
                    {cues.us_vix.toFixed(2)}
                  </span>
                </div>
              )}
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

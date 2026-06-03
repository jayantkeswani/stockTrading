"use client";

import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { formatINR, formatTime, startOfDayIST } from "@/lib/formatters";
import { STRATEGY_LABELS, STATUS_COLORS } from "@/lib/constants";
import type { Signal } from "@/lib/types";
import { SignalHistoryPanel } from "@/components/signals/SignalHistoryPanel";
import { useStore } from "@/store";
import { useShallow } from "zustand/react/shallow";

const VWAP_FACTOR_LABELS: Record<string, string> = {
  bias_alignment: "Bias", vwap_slope_alignment: "Slope", reversal_quality: "Reversal",
  volume_quality: "Volume", rr_ratio_quality: "R:R", oi_support: "OI",
  cpr_narrow_trending: "CPR", vix_regime: "VIX", global_alignment: "Global", time_of_day: "Window",
};
const S5_FACTOR_LABELS: Record<string, string> = {
  vol_factor: "Volume", rvol_factor: "RVOL", bias_factor: "Nifty", phase_factor: "Phase",
  setup_factor: "Setup", rank_factor: "Rank", gap_factor: "Gap", trend_factor: "Trend", oi_factor: "OI",
};

const LONG_TYPES = ["BUY_CE", "BUY_FUT"];

function isLong(t: Signal["signal_type"]) {
  return LONG_TYPES.includes(t);
}

function DirBadge({ signal }: { signal: Signal }) {
  const long = isLong(signal.signal_type);
  const label = signal.instrument_type === "OPTION"
    ? (long ? "▲ CE" : "▼ PE")
    : (long ? "▲ LONG" : "▼ SHORT");
  return (
    <span className={`text-[10px] font-mono font-bold py-0.5 rounded shrink-0 inline-flex items-center justify-center w-[3.25rem] ${
      long ? "bg-profit/10 text-profit" : "bg-loss/10 text-loss"
    }`}>
      {label}
    </span>
  );
}

function ConfidenceFactors({ factors, labelMap }: { factors: Record<string, number>; labelMap: Record<string, string> }) {
  const keys = Object.keys(labelMap).filter((k) => factors[k] !== undefined);
  if (keys.length === 0) return null;
  return (
    <div className="mt-2">
      <p className="text-[9px] font-mono text-text-muted uppercase tracking-wider mb-1">Confidence Factors</p>
      <div className="flex flex-col gap-0.5">
        {keys.map((k) => {
          const pct = Math.round((factors[k] as number) * 100);
          const color = pct >= 70 ? "bg-profit/60" : pct >= 40 ? "bg-accent/60" : "bg-loss/60";
          return (
            <div key={k} className="flex items-center gap-2">
              <span className="text-[10px] font-mono text-text-muted w-16 shrink-0">{labelMap[k]}</span>
              <div className="flex-1 h-1 bg-border/40 rounded-full overflow-hidden">
                <div className={`h-full ${color} rounded-full`} style={{ width: `${pct}%` }} />
              </div>
              <span className="text-[10px] font-mono text-text-muted w-7 text-right">{pct}%</span>
            </div>
          );
        })}
      </div>
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

function SignalCard({ signal }: { signal: Signal }) {
  const [open, setOpen] = useState(false);
  const toggle = () => setOpen((o) => !o);
  const isUpdated = (signal.update_count ?? 0) > 0;
  const ind = (signal.indicators ?? {}) as Record<string, unknown>;
  const confFactors = ind.confidence_factors as Record<string, number> | undefined;
  const supports = ind.ai_key_supports as string[] | undefined;
  const risks = ind.ai_key_risks as string[] | undefined;
  const labelMap = signal.strategy_name === "intraday_futures" ? S5_FACTOR_LABELS : VWAP_FACTOR_LABELS;
  const exec = signal.status === "EXECUTED";
  const expired = signal.status === "EXPIRED";

  return (
    <div className={`rounded-lg border bg-bg-secondary mb-1.5 overflow-hidden ${
      exec ? "border-l-2 border-l-profit border-border" : "border-border"
    } ${expired ? "opacity-60" : ""}`}>
      <button onClick={toggle} className="w-full flex items-center gap-2 px-3 py-2 text-left">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <DirBadge signal={signal} />
            <span className="text-[15px] font-mono font-bold text-text-primary truncate">{signal.symbol}</span>
            {signal.is_permanent_watchlist && (
              <span className="text-[9px] font-mono px-1 py-px rounded border border-accent/40 text-accent/70">P</span>
            )}
            <span className={`text-[9px] font-mono px-1 py-px rounded ${STATUS_COLORS[signal.status] || ""}`}>
              {signal.status}
            </span>
          </div>
          <div className="text-[11px] font-mono text-text-muted mt-0.5 truncate">
            {signal.instrument_type === "OPTION" && signal.strike_price > 0 ? `${signal.strike_price} · ` : "FUT · "}
            {STRATEGY_LABELS[signal.strategy_name] || signal.strategy_name} · {formatTime(signal.generated_at)}
          </div>
        </div>
        {isUpdated && (
          <span className="text-[10px] font-mono px-1.5 py-px rounded bg-accent/15 text-accent border border-accent/30 shrink-0">↻ Updated</span>
        )}
        <span className={`text-text-muted text-[13px] transition-transform ${open ? "rotate-180" : ""}`}>▼</span>
      </button>

      {/* Glance metrics */}
      <div className="flex border-t border-border">
        <GlanceCell k="ENTRY" v={signal.entry_price > 0 ? formatINR(signal.entry_price) : "—"} />
        <GlanceCell k="TARGET" v={signal.target_price && signal.target_price > 0 ? formatINR(signal.target_price) : "—"} cls="text-profit" />
        <GlanceCell k="SL" v={signal.stop_loss > 0 ? formatINR(signal.stop_loss) : "—"} cls="text-loss" />
        <GlanceCell
          k="CONF"
          v={signal.confidence != null ? String(signal.confidence) : "—"}
          cls={signal.confidence != null && signal.confidence >= 70 ? "text-profit" : signal.confidence != null && signal.confidence >= 55 ? "text-accent" : "text-text-muted"}
        />
      </div>

      {open && (
        <div className="px-3 py-2.5 border-t border-border bg-bg-primary animate-fade-in">
          {signal.ai_action && signal.ai_action !== "PROCEED" && (
            <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded border ${
              signal.ai_action === "RECONSIDER" ? "bg-loss/10 text-loss border-loss/30" : "bg-warning/10 text-warning border-warning/30"
            }`}>
              AI: {signal.ai_action.replace(/_/g, " ")}
              {signal.ai_adjustment != null && signal.ai_adjustment !== 0 && ` · ${signal.ai_adjustment > 0 ? "+" : ""}${signal.ai_adjustment}`}
            </span>
          )}
          {signal.ai_summary ? (
            <p className="text-[13px] font-mono text-text-primary leading-relaxed mt-1.5">
              <span className="text-accent">✦</span> {signal.ai_summary}
            </p>
          ) : (
            <p className="text-[13px] font-mono text-text-muted leading-relaxed mt-1.5">{signal.reason}</p>
          )}
          {signal.ai_rationale && (
            <>
              <p className="text-[9px] font-mono text-text-muted uppercase tracking-wider mt-2 mb-0.5">AI Rationale</p>
              <p className="text-[12px] font-mono text-text-secondary leading-relaxed">{signal.ai_rationale}</p>
            </>
          )}
          {(supports?.length || risks?.length) ? (
            <div className="mt-2 space-y-2">
              {supports && supports.length > 0 && (
                <div>
                  <p className="text-[9px] font-mono text-profit uppercase tracking-wider mb-0.5">Supports</p>
                  <ul className="space-y-1">{supports.map((s, i) => (
                    <li key={i} className="text-[11px] font-mono text-text-secondary leading-snug flex gap-1.5">
                      <span className="text-profit/60 shrink-0">•</span><span className="min-w-0">{s}</span>
                    </li>
                  ))}</ul>
                </div>
              )}
              {risks && risks.length > 0 && (
                <div>
                  <p className="text-[9px] font-mono text-loss uppercase tracking-wider mb-0.5">Risks</p>
                  <ul className="space-y-1">{risks.map((r, i) => (
                    <li key={i} className="text-[11px] font-mono text-text-secondary leading-snug flex gap-1.5">
                      <span className="text-loss/60 shrink-0">•</span><span className="min-w-0">{r}</span>
                    </li>
                  ))}</ul>
                </div>
              )}
            </div>
          ) : null}
          {confFactors && <ConfidenceFactors factors={confFactors} labelMap={labelMap} />}
          {signal.blocked_reason && (
            <p className="text-[10px] font-mono text-text-muted italic mt-2">⊘ {signal.blocked_reason}</p>
          )}
          <SignalHistoryPanel signalId={signal.id} />
        </div>
      )}
    </div>
  );
}

/**
 * Mobile Signals tab: confidence slider + Executed/Expired toggles, then a
 * card list of today's signals (collapsed glance: dir, symbol, entry, target,
 * SL, conf; expand: AI insight + details). Fetches today's signals on mount,
 * polls every 15s, and re-fetches on `refreshKey`. Merges live WebSocket
 * signal updates from the store and flags revised cards with an "↻ UPD" badge.
 * Reuses persisted scanner filters from the store. Used by: MobileShell.
 */
export function MobileSignals({ refreshKey }: { refreshKey?: number }) {
  const {
    scannerShowExecuted, setScannerShowExecuted,
    scannerShowExpired, setScannerShowExpired,
    scannerMinConfidence, setScannerMinConfidence,
  } = useStore(useShallow((s) => ({
    scannerShowExecuted: s.scannerShowExecuted, setScannerShowExecuted: s.setScannerShowExecuted,
    scannerShowExpired: s.scannerShowExpired, setScannerShowExpired: s.setScannerShowExpired,
    scannerMinConfidence: s.scannerMinConfidence, setScannerMinConfidence: s.setScannerMinConfidence,
  })));
  const liveSignals = useStore((s) => s.signals); // WebSocket-fed (signal:new / signal:updated)

  const [fetched, setFetched] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = (await api.getSignals({
          generated_since: startOfDayIST(new Date()).toISOString(),
          limit: 200,
        })) as Signal[];
        if (!cancelled) setFetched(data);
      } catch {
        if (!cancelled) setFetched([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    const id = setInterval(load, 15_000);
    return () => { cancelled = true; clearInterval(id); };
  }, [refreshKey]);

  // Merge the REST poll with live WS signals (store wins on shared ids), newest
  // first. WS payloads omit update_count, so carry it over from the polled copy
  // (and a live signal:updated implies ≥1 revision) so the "↻ UPD" badge sticks.
  const merged = useMemo(() => {
    const map = new Map<string, Signal>();
    for (const s of fetched) map.set(s.id, s);
    for (const s of liveSignals) {
      const prior = map.get(s.id);
      map.set(s.id, { ...s, update_count: s.update_count ?? prior?.update_count ?? 0 });
    }
    return Array.from(map.values()).sort(
      (a, b) => new Date(b.generated_at).getTime() - new Date(a.generated_at).getTime(),
    );
  }, [fetched, liveSignals]);

  const displayed = merged.filter((s) => {
    if (s.status === "PENDING") { /* always shown */ }
    else if (s.status === "EXECUTED") { if (!scannerShowExecuted) return false; }
    else if (s.status === "EXPIRED") { if (!scannerShowExpired) return false; }
    else return false; // REJECTED etc. hidden
    if (scannerMinConfidence > 0 && (s.confidence == null || Number(s.confidence) < scannerMinConfidence)) return false;
    return true;
  });

  return (
    <div className="p-3">
      {/* Controls */}
      <div className="sticky top-0 z-10 -mx-3 px-3 pb-2 mb-1 bg-bg-primary border-b border-border">
        <label className="flex items-center gap-2 mb-2">
          <span className="text-[10px] font-mono text-text-muted tracking-wide">CONF</span>
          <input
            type="range" min={0} max={100} step={5} value={scannerMinConfidence}
            onChange={(e) => setScannerMinConfidence(Number(e.target.value))}
            className="flex-1 h-1 accent-accent"
          />
          <span className={`text-[11px] font-mono w-8 ${scannerMinConfidence > 0 ? "text-accent" : "text-text-muted"}`}>
            {scannerMinConfidence > 0 ? `${scannerMinConfidence}%` : "any"}
          </span>
        </label>
        <div className="flex gap-1.5">
          <button
            onClick={() => setScannerShowExecuted(!scannerShowExecuted)}
            className={`text-[12px] font-mono px-2.5 py-1 rounded border ${
              scannerShowExecuted ? "bg-profit/10 text-profit border-profit/30" : "bg-bg-tertiary text-text-secondary border-border"
            }`}
          >
            + Executed
          </button>
          <button
            onClick={() => setScannerShowExpired(!scannerShowExpired)}
            className={`text-[12px] font-mono px-2.5 py-1 rounded border ${
              scannerShowExpired ? "bg-accent/10 text-accent border-accent/30" : "bg-bg-tertiary text-text-secondary border-border"
            }`}
          >
            + Expired
          </button>
        </div>
      </div>

      {loading ? (
        <div className="py-8 text-center text-text-muted text-[13px] font-mono">loading…</div>
      ) : displayed.length === 0 ? (
        <div className="py-8 text-center text-text-muted text-[13px] font-mono">no signals</div>
      ) : (
        displayed.map((s) => <SignalCard key={s.id} signal={s} />)
      )}
    </div>
  );
}

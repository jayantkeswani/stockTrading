"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/formatters";
import type { IntradayHunterRun, IntradayHunterHistoryItem, IntradayHunterBasketLeg } from "@/lib/types";
import { StatusChip } from "@/components/intraday-hunter/badges";
import { ThesisCard } from "@/components/intraday-hunter/ThesisCard";
import { DecisionCard } from "@/components/intraday-hunter/DecisionCard";
import { BasketCard } from "@/components/intraday-hunter/BasketCard";
import { HistoryTimeline } from "@/components/intraday-hunter/HistoryTimeline";
import { HistoryDetailModal } from "@/components/intraday-hunter/HistoryDetailModal";
import { MobileHunterV2 } from "./MobileHunterV2";

const POLL_MS = 20000;
const TAB_KEY = "ih_tab";
type IhTab = "v1" | "v2";

/**
 * Mobile Intraday Hunter tab: the phone equivalent of the desktop
 * `/intraday-hunter` page (discretionary index-options SUGGESTER — suggestions
 * only, never auto-executed). Reuses the shared ThesisCard / DecisionCard /
 * HistoryTimeline cards (already responsive) stacked in a single column.
 * Polls today + 30-day history every 20s and on `refreshKey`. Used by:
 * MobileShell.
 */
export function MobileHunter({ refreshKey }: { refreshKey?: number }) {
  const [tab, setTab] = useState<IhTab>("v1");
  useEffect(() => {
    try {
      const saved = localStorage.getItem(TAB_KEY);
      if (saved === "v1" || saved === "v2") setTab(saved);
    } catch {}
  }, []);
  const pick = (t: IhTab) => {
    setTab(t);
    try {
      localStorage.setItem(TAB_KEY, t);
    } catch {}
  };
  const [run, setRun] = useState<IntradayHunterRun | null>(null);
  const [history, setHistory] = useState<IntradayHunterHistoryItem[]>([]);
  const [basket, setBasket] = useState<IntradayHunterBasketLeg[]>([]);
  const [historyDate, setHistoryDate] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [today, hist, legs] = await Promise.all([
        api.getIntradayHunterToday(),
        api.getIntradayHunterHistory(30),
        api.getIntradayHunterBasket(),
      ]);
      setRun(today);
      setHistory(hist);
      setBasket(legs);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load, refreshKey]);

  const runCall = useCallback(
    async (which: "call1" | "call2") => {
      setBusy(which);
      setError(null);
      try {
        if (which === "call1") await api.runIntradayHunterCall1();
        else await api.runIntradayHunterCall2();
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : "run failed");
      } finally {
        setBusy(null);
      }
    },
    [load]
  );

  const switcher = (
    <div className="flex rounded-lg border border-border bg-bg-secondary p-0.5 text-[12px] font-mono">
      {(["v1", "v2"] as const).map((t) => (
        <button
          key={t}
          onClick={() => pick(t)}
          className={`flex-1 py-1 rounded ${tab === t ? "bg-bg-tertiary text-text-primary" : "text-text-muted"}`}
        >
          {t}
        </button>
      ))}
    </div>
  );

  if (tab === "v2") {
    return (
      <div className="p-3 space-y-3">
        {switcher}
        <MobileHunterV2 refreshKey={refreshKey} />
      </div>
    );
  }

  return (
    <div className="p-3 space-y-3">
      {switcher}
      {/* Status bar */}
      <div className="rounded-lg border border-border bg-bg-secondary px-3 py-2 space-y-2">
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-[12px] font-mono font-medium text-text-secondary uppercase tracking-wider">
            Intraday Hunter
          </h1>
          <span className="text-[10px] font-mono px-1.5 py-px rounded bg-bg-tertiary text-text-muted">
            SUGGEST only
          </span>
          {run && <StatusChip status={run.status} />}
          {run?.is_expiry && (
            <span className="text-[10px] font-mono px-1.5 py-px rounded bg-warning/15 text-warning">
              {run.expiry_index} EXPIRY
            </span>
          )}
          {run?.updated_at && (
            <span className="text-[10px] font-mono text-text-muted ml-auto">upd {formatTime(run.updated_at)}</span>
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={() => runCall("call1")}
            disabled={busy != null}
            className="text-[11px] font-mono px-2 py-1 rounded border border-border text-text-secondary bg-bg-tertiary disabled:opacity-40"
          >
            {busy === "call1" ? "running…" : "Run Call 1"}
          </button>
          <button
            onClick={() => runCall("call2")}
            disabled={busy != null}
            className="text-[11px] font-mono px-2 py-1 rounded border border-border text-text-secondary bg-bg-tertiary disabled:opacity-40"
          >
            {busy === "call2" ? "running…" : "Force Call 2"}
          </button>
        </div>
      </div>

      {error && (
        <div className="px-3 py-1.5 bg-loss/10 border border-loss/30 rounded text-[11px] font-mono text-loss">
          {error}
        </div>
      )}

      {/* Suggestion-only disclaimer */}
      <p className="text-[10px] font-mono text-text-muted px-1 leading-relaxed">
        Suggestions are logged for live validation and never auto-executed. Index direction ≠ option win-rate —
        no capital until the paper book confirms real-premium profit.
      </p>

      <ThesisCard call1={run?.call1_json ?? null} generatedAt={run?.created_at} />
      {run && <DecisionCard run={run} />}
      <BasketCard legs={basket} onClosed={load} />
      <HistoryTimeline items={history} onSelect={setHistoryDate} />

      {historyDate && (
        <HistoryDetailModal date={historyDate} onClose={() => setHistoryDate(null)} />
      )}
    </div>
  );
}

"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/formatters";
import type { IntradayHunterRun, IntradayHunterHistoryItem } from "@/lib/types";
import { StatusChip } from "@/components/intraday-hunter/badges";
import { ThesisCard } from "@/components/intraday-hunter/ThesisCard";
import { DecisionCard } from "@/components/intraday-hunter/DecisionCard";
import { HistoryTimeline } from "@/components/intraday-hunter/HistoryTimeline";

const POLL_MS = 20000;

export default function IntradayHunterPage() {
  const [run, setRun] = useState<IntradayHunterRun | null>(null);
  const [history, setHistory] = useState<IntradayHunterHistoryItem[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [today, hist] = await Promise.all([
        api.getIntradayHunterToday(),
        api.getIntradayHunterHistory(30),
      ]);
      setRun(today);
      setHistory(hist);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [load]);

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

  return (
    <div className="space-y-3">
      {/* Status bar */}
      <div className="flex flex-wrap items-center gap-3 px-3 py-1.5 bg-bg-secondary border border-border rounded">
        <h1 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          Intraday Hunter — Index Options
        </h1>
        <span className="text-[10px] font-mono px-1.5 py-px rounded bg-bg-tertiary text-text-muted">
          SUGGEST only · manual exec
        </span>
        <div className="w-px h-4 bg-border" />
        {run && <StatusChip status={run.status} />}
        {run?.is_expiry && (
          <span className="text-[10px] font-mono px-1.5 py-px rounded bg-warning/15 text-warning">
            {run.expiry_index} EXPIRY
          </span>
        )}
        {run?.updated_at && (
          <span className="text-[10px] font-mono text-text-muted">upd {formatTime(run.updated_at)}</span>
        )}

        <div className="flex-1" />

        <div className="flex items-center gap-1.5">
          <button
            onClick={() => runCall("call1")}
            disabled={busy != null}
            className="text-[10px] font-mono px-2 py-0.5 rounded border border-border text-text-secondary hover:bg-bg-tertiary disabled:opacity-40"
          >
            {busy === "call1" ? "running…" : "Run Call 1"}
          </button>
          <button
            onClick={() => runCall("call2")}
            disabled={busy != null}
            className="text-[10px] font-mono px-2 py-0.5 rounded border border-border text-text-secondary hover:bg-bg-tertiary disabled:opacity-40"
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
      <p className="text-[10px] font-mono text-text-muted px-1">
        Suggestions are logged for live validation and never auto-executed. Index direction ≠ option win-rate —
        no capital until the paper book confirms real-premium profit.
      </p>

      <div className="grid grid-cols-12 gap-3">
        <div className="col-span-12 lg:col-span-8 space-y-3">
          <ThesisCard call1={run?.call1_json ?? null} generatedAt={run?.created_at} />
          {run && <DecisionCard run={run} />}
        </div>
        <div className="col-span-12 lg:col-span-4">
          <HistoryTimeline items={history} />
        </div>
      </div>
    </div>
  );
}

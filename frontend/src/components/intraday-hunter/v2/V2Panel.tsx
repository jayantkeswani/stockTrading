"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { IntradayHunterRun, IntradayHunterHistoryItem, IhV2Call1, IhV2Call2Entry } from "@/lib/types";
import { StatusChip } from "../badges";
import { HistoryTimeline } from "../HistoryTimeline";
import { PlanCard } from "./PlanCard";
import { DecisionLog } from "./DecisionLog";
import { BasketCard } from "./BasketCard";
import { TeacherCard } from "./TeacherCard";
import { LedgerCard } from "./LedgerCard";
import { GradesCard } from "./GradesCard";
import { WeeklyReviewCard } from "./WeeklyReviewCard";
import { V2HistoryModal } from "./V2HistoryModal";

const POLL_MS = 15000;

/** Intraday Hunter v2 tab body: plan, decision log, basket, teacher, ledger, grades, weekly review, history. Used by: intraday-hunter/page */
export function V2Panel() {
  const [run, setRun] = useState<IntradayHunterRun | null>(null);
  const [history, setHistory] = useState<IntradayHunterHistoryItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [histDate, setHistDate] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [t, h] = await Promise.all([api.getIhV2Today(), api.getIhV2History(30)]);
      setRun(t);
      setHistory(h);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, []);

  useEffect(() => {
    const first = setTimeout(load, 0);
    const t = setInterval(load, POLL_MS);
    return () => {
      clearTimeout(first);
      clearInterval(t);
    };
  }, [load]);

  const date = run?.trading_date ?? new Date().toISOString().slice(0, 10);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-3 px-3 py-1.5 bg-bg-secondary border border-border rounded">
        <span className="text-xs font-mono text-text-secondary uppercase tracking-wider">v2 · {date}</span>
        {run && <StatusChip status={run.status} />}
        <span className="text-[10px] font-mono px-1.5 py-px rounded bg-bg-tertiary text-text-muted">parallel paper strategy</span>
      </div>
      {error && (
        <div className="px-3 py-1.5 bg-loss/10 border border-loss/30 rounded text-[11px] font-mono text-loss">{error}</div>
      )}
      <div className="grid grid-cols-12 gap-3">
        <div className="col-span-12 lg:col-span-8 space-y-3">
          <PlanCard call1={run?.call1_json as IhV2Call1 | null | undefined} />
          <DecisionLog history={(run?.call2_history ?? []) as IhV2Call2Entry[]} />
          <BasketCard />
          <TeacherCard date={date} run={run} />
          <LedgerCard />
          <GradesCard />
          <WeeklyReviewCard />
        </div>
        <div className="col-span-12 lg:col-span-4">
          <HistoryTimeline items={history} onSelect={setHistDate} />
        </div>
      </div>
      {histDate && <V2HistoryModal date={histDate} onClose={() => setHistDate(null)} />}
    </div>
  );
}

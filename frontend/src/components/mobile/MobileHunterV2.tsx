"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { IhTeacher, IhV2Call1, IhV2Call2Entry, IntradayHunterRun } from "@/lib/types";
import { StatusChip, DirectionBadge, confidenceColor } from "../intraday-hunter/badges";
import { PlanCard } from "../intraday-hunter/v2/PlanCard";
import { DecisionLog } from "../intraday-hunter/v2/DecisionLog";
import { Section, Empty } from "../intraday-hunter/v2/common";
import { WeeklyReviewCard } from "../intraday-hunter/v2/WeeklyReviewCard";
import { MobileV2Basket } from "./MobileV2Basket";

const POLL_MS = 15000;

function MobileTeacher({ date }: { date: string }) {
  const [t, setT] = useState<IhTeacher | null>(null);
  const [state, setState] = useState<"loading" | "none" | "ok" | "error">("loading");
  useEffect(() => {
    let cancelled = false;
    api
      .getIhTeacher(date)
      .then((r) => {
        if (cancelled) return;
        setT(r);
        setState("ok");
      })
      .catch((e) => {
        if (cancelled) return;
        setState(/no teacher data/i.test(e instanceof Error ? e.message : "") ? "none" : "error");
      });
    return () => {
      cancelled = true;
    };
  }, [date]);
  const plan = t?.plan;
  return (
    <Section title="Teacher plan" right={t?.status && <span className="text-[10px] font-mono text-text-muted">{t.status}</span>}>
      {state === "loading" ? (
        <Empty>loading…</Empty>
      ) : state === "none" ? (
        <Empty>no teacher data yet</Empty>
      ) : state === "error" ? (
        <p className="text-[11px] font-mono text-loss">failed to load teacher</p>
      ) : plan ? (
        <div className="space-y-1.5 text-[11px] font-mono">
          <div className="grid grid-cols-2 gap-2">
            {([["gap up", plan.gap_up_side], ["flat", plan.flat_side], ["gap down", plan.gap_down_side], ["bias", plan.bias]] as const).map(([k, v]) => (
              <div key={k} className="border border-border rounded px-2 py-1">
                <div className="text-text-muted text-[10px] uppercase">{k}</div>
                <div className="text-text-primary">{v ?? "—"}</div>
              </div>
            ))}
          </div>
          {plan.summary && <p className="text-text-secondary leading-relaxed line-clamp-6">{plan.summary}</p>}
          {t?.live && (
            <div className="text-text-muted">his live side <b className="text-text-primary">{t.live.side ?? "—"}</b> · total {t.live.total_pnl ?? "—"}</div>
          )}
        </div>
      ) : (
        <Empty>teacher record has no plan</Empty>
      )}
    </Section>
  );
}

/**
 * Phone view of Intraday Hunter v2: status strip, plan (Call 1), Call 2 decision log,
 * basket with emergency close, teacher plan. Polls getIhV2Today every 15s and on `refreshKey`.
 * Reuses desktop PlanCard / DecisionLog (wrap-friendly). Used by: MobileHunter
 */
export function MobileHunterV2({ refreshKey }: { refreshKey?: number }) {
  const [run, setRun] = useState<IntradayHunterRun | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setRun(await api.getIhV2Today());
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
  }, [load, refreshKey]);

  const date = run?.trading_date ?? new Date().toISOString().slice(0, 10);

  return (
    <div className="space-y-3 min-w-0">
      <div className="flex flex-wrap items-center gap-2 px-3 py-1.5 bg-bg-secondary border border-border rounded">
        <span className="text-xs font-mono text-text-secondary uppercase tracking-wider">v2 · {date}</span>
        {run && <StatusChip status={run.status} />}
        {run?.direction && <DirectionBadge direction={run.direction} />}
        {run?.confidence != null && (
          <span className={`text-[11px] font-mono ${confidenceColor(run.confidence)}`}>{run.confidence}</span>
        )}
      </div>
      {error && (
        <div className="px-3 py-1.5 bg-loss/10 border border-loss/30 rounded text-[11px] font-mono text-loss">{error}</div>
      )}
      <PlanCard call1={run?.call1_json as IhV2Call1 | null | undefined} />
      <DecisionLog history={(run?.call2_history ?? []) as IhV2Call2Entry[]} />
      <MobileV2Basket refreshKey={refreshKey} />
      <MobileTeacher date={date} />
      <WeeklyReviewCard />
    </div>
  );
}

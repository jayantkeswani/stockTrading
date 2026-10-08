"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { IntradayHunterRun, IhV2Call1, IhV2Call2Entry } from "@/lib/types";
import { StatusChip } from "../badges";
import { PlanCard } from "./PlanCard";
import { DecisionLog } from "./DecisionLog";

/** Prior-day v2 run detail (plan + decision log) from api.getIhV2Run. Used by: V2Panel */
export function V2HistoryModal({ date, onClose }: { date: string; onClose: () => void }) {
  const [run, setRun] = useState<IntradayHunterRun | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    let cancelled = false;
    api.getIhV2Run(date).then((r) => !cancelled && setRun(r)).catch((e) => !cancelled && setError(e instanceof Error ? e.message : "failed to load"));
    return () => {
      cancelled = true;
    };
  }, [date]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto py-8" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div className="relative w-[min(820px,92vw)] rounded border border-border bg-bg-elevated shadow-xl" onClick={(e) => e.stopPropagation()}>
        <div className="sticky top-0 z-10 px-4 py-2.5 border-b border-border bg-bg-elevated flex items-center gap-2">
          <h2 className="text-sm font-mono font-medium text-text-primary">{date} · v2</h2>
          {run && <StatusChip status={run.decision || run.status} />}
          <button onClick={onClose} className="ml-auto text-text-muted hover:text-text-primary text-sm font-mono">✕</button>
        </div>
        <div className="p-3 space-y-3">
          {error ? (
            <p className="text-xs font-mono text-loss py-6 text-center">{error}</p>
          ) : !run ? (
            <p className="text-xs font-mono text-text-muted py-6 text-center lowercase">loading…</p>
          ) : (
            <>
              <PlanCard call1={run.call1_json as IhV2Call1 | null} />
              <DecisionLog history={(run.call2_history ?? []) as IhV2Call2Entry[]} />
              {run.realized_outcome_note && (
                <p className="text-[11px] font-mono text-text-secondary italic px-1">{run.realized_outcome_note}</p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

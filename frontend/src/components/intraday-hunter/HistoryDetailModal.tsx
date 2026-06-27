"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { IntradayHunterRun } from "@/lib/types";
import { StatusChip } from "./badges";
import { ThesisCard } from "./ThesisCard";
import { DecisionCard } from "./DecisionCard";

/**
 * Full prior-day detail popup for a selected History row: fetches the full run by
 * date (`api.getIntradayHunterRun`) and reuses ThesisCard (Call 1) + DecisionCard
 * (Call 2 + charts), plus the realized-outcome note. Modal overlay; Escape or
 * click-outside / ✕ closes. Used by: intraday-hunter/page, MobileHunter.
 */
export function HistoryDetailModal({ date, onClose }: { date: string; onClose: () => void }) {
  const [run, setRun] = useState<IntradayHunterRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  useEffect(() => {
    // `date` is fixed for the modal's lifetime (the overlay blocks re-selecting a
    // row behind it), so no synchronous loading reset is needed — start at true.
    let cancelled = false;
    api
      .getIntradayHunterRun(date)
      .then((r) => {
        if (!cancelled) {
          setRun(r);
          setLoading(false);
        }
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "failed to load");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [date]);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto py-8" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative w-[min(720px,92vw)] rounded border border-border bg-bg-elevated shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="sticky top-0 z-10 px-4 py-2.5 border-b border-border bg-bg-elevated flex items-center gap-2">
          <h2 className="text-sm font-mono font-medium text-text-primary">{date}</h2>
          {run?.is_expiry && (
            <span className="text-[10px] font-mono px-1.5 py-px rounded bg-warning/15 text-warning">
              {run.expiry_index} EXP
            </span>
          )}
          {run && <StatusChip status={run.decision || run.status} />}
          <button
            onClick={onClose}
            className="ml-auto text-text-muted hover:text-text-primary text-sm font-mono"
          >
            ✕
          </button>
        </div>

        <div className="p-3 space-y-3">
          {loading ? (
            <p className="text-xs font-mono text-text-muted px-1 py-6 text-center lowercase">loading…</p>
          ) : error ? (
            <p className="text-xs font-mono text-loss px-1 py-6 text-center">{error}</p>
          ) : run ? (
            <>
              <ThesisCard call1={run.call1_json} generatedAt={run.created_at} />
              <DecisionCard run={run} />
              {run.realized_outcome_note && (
                <p className="text-[11px] font-mono text-text-secondary italic px-1">
                  {run.realized_outcome_note}
                </p>
              )}
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

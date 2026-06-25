"use client";

import type { IHCall1 } from "@/lib/types";
import { formatTime } from "@/lib/formatters";

const INDICES = ["NIFTY", "BANKNIFTY", "SENSEX"];

const SECTION_HDR = "text-xs font-mono font-medium text-text-secondary uppercase tracking-wider";

/** Call 1 — the pre-open thesis (trapped side, plan, per-index levels). */
export function ThesisCard({ call1, generatedAt }: { call1: IHCall1 | null; generatedAt?: string | null }) {
  return (
    <div className="bg-bg-secondary border border-border rounded p-3 space-y-3">
      <div className="flex items-center gap-2">
        <h2 className={SECTION_HDR}>Pre-Market Thesis</h2>
        <span className="text-[10px] font-mono text-text-muted">Call 1 · ~08:45</span>
        {generatedAt && (
          <span className="text-[10px] font-mono text-text-muted ml-auto">{formatTime(generatedAt)}</span>
        )}
      </div>

      {!call1 ? (
        <p className="text-xs font-mono text-text-muted lowercase">thesis not generated yet</p>
      ) : call1.error ? (
        <p className="text-xs font-mono text-loss">{String(call1.error)}</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            {call1.trapped_side && (
              <span className="text-[10px] font-mono px-1.5 py-px rounded bg-bg-tertiary text-text-primary">
                trapped: <span className="text-accent">{call1.trapped_side}</span>
              </span>
            )}
            {call1.regime_lean && (
              <span className="text-[10px] font-mono px-1.5 py-px rounded bg-bg-tertiary text-text-secondary">
                {call1.regime_lean}
              </span>
            )}
            {call1.preferred_action_lean && (
              <span className="text-[10px] font-mono px-1.5 py-px rounded bg-accent/10 text-accent">
                lean: {call1.preferred_action_lean}
              </span>
            )}
          </div>

          {call1.thesis && (
            <p className="text-sm font-mono text-text-primary leading-relaxed">{call1.thesis}</p>
          )}

          {call1.conditional_plan && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {call1.conditional_plan.if_gap_down && (
                <PlanCell label="if gap-down" value={call1.conditional_plan.if_gap_down} />
              )}
              {call1.conditional_plan.if_flat_or_gap_up && (
                <PlanCell label="if flat / gap-up" value={call1.conditional_plan.if_flat_or_gap_up} />
              )}
            </div>
          )}

          {(call1.trigger_levels || call1.invalidation) && (
            <div className="space-y-1">
              <div className="grid grid-cols-4 gap-2 text-[10px] font-mono text-text-muted uppercase tracking-wider">
                <span>index</span>
                <span className="text-right">trigger</span>
                <span className="text-right">invalidation</span>
                <span className="text-right">exp. range</span>
              </div>
              {INDICES.map((idx) => (
                <div key={idx} className="grid grid-cols-4 gap-2 text-xs font-mono">
                  <span className="text-text-secondary">{idx}</span>
                  <span className="text-right text-text-primary">{fmtLevel(call1.trigger_levels?.[idx])}</span>
                  <span className="text-right text-loss/80">{fmtLevel(call1.invalidation?.[idx])}</span>
                  <span className="text-right text-text-muted">—</span>
                </div>
              ))}
            </div>
          )}

          {call1.expected_range_note && (
            <p className="text-[11px] font-mono text-text-muted italic">{call1.expected_range_note}</p>
          )}
          {call1.notes && (
            <p className="text-[11px] font-mono text-text-secondary">📌 {call1.notes}</p>
          )}
        </>
      )}
    </div>
  );
}

function PlanCell({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-bg-tertiary/50 border border-border rounded px-2 py-1.5">
      <div className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-0.5">{label}</div>
      <div className="text-xs font-mono text-text-primary">{value}</div>
    </div>
  );
}

function fmtLevel(v: number | undefined): string {
  if (v == null || v === 0) return "—";
  return v.toLocaleString("en-IN");
}

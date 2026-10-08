"use client";

import type { IhV2Call1 } from "@/lib/types";
import { formatLatency } from "@/lib/ihV2";
import { Section, Empty, Val } from "./common";

/** v2 Call 1 plan: bias, thesis, key pools, plan-by-opening, teacher alignment, lessons. Used by: V2Panel, V2HistoryModal */
export function PlanCard({ call1 }: { call1: IhV2Call1 | null | undefined }) {
  return (
    <Section
      title="Plan (Call 1)"
      right={
        call1 && (
          <>
            {call1._teacher_plan_missing && (
              <span className="text-[10px] font-mono px-1.5 py-px rounded bg-warning/15 text-warning">
                teacher plan missing
              </span>
            )}
            {call1._latency_ms != null && (
              <span className="text-[10px] font-mono text-text-muted">{formatLatency(call1._latency_ms)}</span>
            )}
          </>
        )
      }
    >
      {!call1 ? (
        <Empty>no plan yet — call 1 has not run today</Empty>
      ) : call1.error ? (
        <p className="text-[11px] font-mono text-loss">{call1.error}</p>
      ) : (
        <div className="space-y-2 text-[11px] font-mono">
          <div className="flex gap-2 items-baseline">
            <span className="text-text-muted">bias</span>
            <span className="text-accent font-medium">{call1.bias ?? "—"}</span>
          </div>
          {call1.thesis && <p className="text-text-primary leading-relaxed">{call1.thesis}</p>}
          {call1.key_pools && (
            <div>
              <div className="text-text-muted uppercase text-[10px] mb-0.5">key pools</div>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                {Object.entries(call1.key_pools).map(([idx, pools]) => (
                  <div key={idx} className="border border-border rounded px-2 py-1">
                    <div className="text-text-secondary font-medium">{idx}</div>
                    <Val v={pools} />
                  </div>
                ))}
              </div>
            </div>
          )}
          {call1.plan_by_opening && (
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
              {(["gap_up", "flat", "gap_down"] as const).map((k) => (
                <div key={k} className="border border-border rounded px-2 py-1">
                  <div className="text-text-muted uppercase text-[10px]">{k.replace("_", " ")}</div>
                  <Val v={call1.plan_by_opening?.[k]} />
                </div>
              ))}
            </div>
          )}
          {call1.teacher_alignment != null && (
            <div>
              <div className="text-text-muted uppercase text-[10px]">teacher alignment</div>
              <Val v={call1.teacher_alignment} />
            </div>
          )}
          {call1.lessons_applied != null && (
            <div>
              <div className="text-text-muted uppercase text-[10px]">lessons applied</div>
              <Val v={call1.lessons_applied} />
            </div>
          )}
        </div>
      )}
    </Section>
  );
}

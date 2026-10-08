"use client";

import { useState } from "react";
import type { IhV2Call2Entry } from "@/lib/types";
import { formatLatency, gateChipClass } from "@/lib/ihV2";
import { StatusChip, DirectionBadge, confidenceColor } from "../badges";
import { Section, Empty, Val } from "./common";

function fmtAt(at?: string): string {
  if (!at) return "—";
  const m = at.match(/T(\d{2}:\d{2}(?::\d{2})?)/);
  return m ? m[1] : at;
}

function Gate({ label, verdict }: { label: string; verdict?: string }) {
  return (
    <span className={`text-[9px] font-mono px-1 py-px rounded ${gateChipClass(verdict)}`}>
      {label} {verdict ?? "NA"}
    </span>
  );
}

/** v2 Call 2 per-minute decision log (09:16-09:25). Used by: V2Panel, V2HistoryModal */
export function DecisionLog({ history }: { history: IhV2Call2Entry[] }) {
  const [open, setOpen] = useState<Set<number>>(new Set());
  const toggle = (i: number) =>
    setOpen((s) => {
      const n = new Set(s);
      if (n.has(i)) n.delete(i);
      else n.add(i);
      return n;
    });
  return (
    <Section title="Decision log (Call 2)" right={<span className="text-[10px] font-mono text-text-muted">{history.length} calls</span>}>
      {history.length === 0 ? (
        <Empty>no decisions yet — watcher runs every minute 09:16–09:25</Empty>
      ) : (
        <div className="space-y-1.5 max-h-[520px] overflow-y-auto">
          {[...history].reverse().map((h, ri) => {
            const i = history.length - 1 - ri;
            const gates = h._gates ?? {};
            return (
              <div key={i} className="border border-border rounded px-2 py-1.5 space-y-1 text-[11px] font-mono">
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-text-primary">{fmtAt(h._at)}</span>
                  <StatusChip status={h.decision ?? "PENDING"} />
                  <DirectionBadge direction={h.direction} />
                  {h.confidence != null && <span className={confidenceColor(h.confidence)}>{h.confidence}</span>}
                  {h._opening_type && <span className="text-text-muted">{h._opening_type}</span>}
                  <span className="ml-auto text-text-muted">
                    {h._model ?? ""} {formatLatency(h._latency_ms)}
                    {h._hook_to_decision_ms != null && ` · hook→dec ${formatLatency(h._hook_to_decision_ms)}`}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-text-secondary">
                  {h.pool_broken && <span>broken: {h.pool_broken}</span>}
                  {h.next_pool_target != null && <span>next: {String(h.next_pool_target)}</span>}
                  {h.skip_reason_code && <span className="text-warning">skip: {h.skip_reason_code}</span>}
                </div>
                <div className="flex items-center gap-1.5">
                  <Gate label="plan" verdict={gates.plan as string | undefined} />
                  <Gate label="oi" verdict={gates.oi as string | undefined} />
                  <span className="text-[9px] text-text-muted">shadow-only</span>
                </div>
                {h._emitted && h._emitted.length > 0 && (
                  <div className="border border-profit/30 rounded px-1.5 py-1 text-profit">
                    emitted {h._emitted.length} leg(s)
                    <div className="text-text-secondary">
                      <Val v={h._emitted} />
                    </div>
                  </div>
                )}
                {h.rationale && (
                  <div>
                    <button onClick={() => toggle(i)} className="text-accent hover:underline">
                      {open.has(i) ? "hide rationale" : "rationale"}
                    </button>
                    {open.has(i) && <p className="mt-1 text-text-secondary leading-relaxed">{h.rationale}</p>}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </Section>
  );
}

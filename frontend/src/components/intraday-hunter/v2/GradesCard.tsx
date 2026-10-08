"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { IhV2Grade } from "@/lib/types";
import { formatPnl } from "@/lib/ihV2";
import { Section, Empty, pnlCls } from "./common";

/** Daily post-mortem grades (PRELIM/FINAL); click a row to expand per-arm outcomes. Used by: V2Panel */
export function GradesCard() {
  const [rows, setRows] = useState<IhV2Grade[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    api.getIhV2Grades(20).then(setRows).catch((e) => setError(e instanceof Error ? e.message : "failed"));
  }, []);

  return (
    <Section title="Grades">
      {error ? (
        <p className="text-[11px] font-mono text-loss">{error}</p>
      ) : !rows || rows.length === 0 ? (
        <Empty>no graded days yet</Empty>
      ) : (
        <div className="space-y-1 text-[11px] font-mono">
          {rows.map((g) => {
            const d = g.trading_date ?? g.date ?? "";
            const isOpen = open === d;
            return (
              <div key={d} className="border border-border rounded">
                <button
                  onClick={() => setOpen(isOpen ? null : d)}
                  className="w-full text-left px-2 py-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 hover:bg-bg-tertiary/40"
                >
                  <span className="text-text-primary">{d}</span>
                  <span className={`text-[9px] px-1 py-px rounded ${g.status === "FINAL" ? "bg-profit/15 text-profit" : "bg-warning/15 text-warning"}`}>
                    {g.status}
                  </span>
                  <span className="text-text-muted">mkt {g.market?.clean_side ?? "—"} / {g.market?.opening ?? "—"}</span>
                  <span>v2 {g.v2?.side ?? "—"} {g.v2?.decision ?? ""} <span className={pnlCls(g.v2?.yolo_net_pnl)}>{formatPnl(g.v2?.yolo_net_pnl)}</span></span>
                  <span>teacher {g.teacher?.side ?? "—"} <span className={pnlCls(g.teacher?.pnl)}>{formatPnl(g.teacher?.pnl)}</span></span>
                  {g.lesson?.one_line_takeaway && (
                    <span className="basis-full text-text-secondary italic">{g.lesson.one_line_takeaway}</span>
                  )}
                </button>
                {isOpen && (
                  <div className="px-2 pb-1.5 overflow-x-auto">
                    {g.arms && Object.keys(g.arms).length > 0 ? (
                      <table className="w-full text-left">
                        <thead className="text-text-muted text-[10px] uppercase">
                          <tr>{["Arm", "Side", "Decision at", "Right", "CF P&L", "Exit"].map((h) => <th key={h} className="font-normal pr-3">{h}</th>)}</tr>
                        </thead>
                        <tbody>
                          {Object.entries(g.arms).map(([arm, a]) => (
                            <tr key={arm} className="border-t border-border/50">
                              <td className="pr-3 text-text-primary">{arm}</td>
                              <td className="pr-3">{a.side ?? "—"}</td>
                              <td className="pr-3">{a.decision_at ?? "—"}</td>
                              <td className={`pr-3 ${a.right_side == null ? "" : a.right_side ? "text-profit" : "text-loss"}`}>
                                {a.right_side == null ? "—" : a.right_side ? "yes" : "no"}
                              </td>
                              <td className={`pr-3 ${pnlCls(a.cf?.pnl)}`}>{formatPnl(a.cf?.pnl)}</td>
                              <td className="text-text-secondary">{a.cf?.exit_reason ?? "—"} {a.cf?.exit_time ?? ""}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    ) : (
                      <Empty>no arm detail</Empty>
                    )}
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

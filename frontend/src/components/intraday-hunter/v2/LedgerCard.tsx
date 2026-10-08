"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { IhV2Ledger, IhV2LedgerArm } from "@/lib/types";
import { formatPnl } from "@/lib/ihV2";
import { Section, Empty, pnlCls } from "./common";

const ARMS = ["rule_a", "plan_side", "oi_flow_side", "v1", "v2_llm", "jev", "teacher"];
const pct = (v: number | null | undefined) => (v == null ? "—" : `${v.toFixed(0)}%`);
const f2 = (v: number | null | undefined) => (v == null ? "—" : v.toFixed(2));

/** Rolling arm ledger (counterfactual P&L by arm) with 20/60-day toggle and gates what-if row. Used by: V2Panel */
export function LedgerCard() {
  const [data, setData] = useState<IhV2Ledger | null>(null);
  const [win, setWin] = useState<"last_20" | "last_60">("last_20");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.getIhV2Ledger().then(setData).catch((e) => setError(e instanceof Error ? e.message : "failed"));
  }, []);

  const w = data?.[win];
  const days = w?._window?.days ?? 0;
  const g = w?._gates;

  return (
    <Section
      title="Ledger"
      right={
        <div className="flex gap-1">
          {(["last_20", "last_60"] as const).map((k) => (
            <button
              key={k}
              onClick={() => setWin(k)}
              className={`text-[10px] font-mono px-1.5 py-px rounded border ${win === k ? "border-accent text-accent" : "border-border text-text-muted"}`}
            >
              {k === "last_20" ? "20d" : "60d"}
            </button>
          ))}
        </div>
      }
    >
      {error ? (
        <p className="text-[11px] font-mono text-loss">{error}</p>
      ) : !w || days === 0 ? (
        <Empty>no graded days yet</Empty>
      ) : (
        <div className="space-y-2 text-[11px] font-mono overflow-x-auto">
          <div className="text-text-muted">
            {days} days · {w._window?.from} → {w._window?.to}
          </div>
          <table className="w-full text-left">
            <thead className="text-text-muted text-[10px] uppercase">
              <tr>
                {["Arm", "n", "Right side", "n cf", "Win", "Mean cf P&L", "t", "1st half", "2nd half"].map((h) => (
                  <th key={h} className="font-normal pr-3 py-0.5">{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ARMS.map((a) => {
                const r = w[a] as IhV2LedgerArm | undefined;
                if (!r) return null;
                return (
                  <tr key={a} className="border-t border-border/50">
                    <td className="pr-3 py-0.5 text-text-primary">{a}</td>
                    <td className="pr-3">{r.n}</td>
                    <td className="pr-3">{pct(r.right_side_pct)}</td>
                    <td className="pr-3">{r.n_cf}</td>
                    <td className="pr-3">{pct(r.win_pct)}</td>
                    <td className={`pr-3 ${pnlCls(r.mean_cf_pnl)}`}>{formatPnl(r.mean_cf_pnl)}</td>
                    <td className="pr-3">{f2(r.t_stat)}</td>
                    <td className={`pr-3 ${pnlCls(r.first_half_mean)}`}>{formatPnl(r.first_half_mean)}</td>
                    <td className={pnlCls(r.second_half_mean)}>{formatPnl(r.second_half_mean)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {g && (
            <div className="border border-border rounded px-2 py-1 flex flex-wrap gap-x-4">
              <span className="text-text-muted">gates what-if (net):</span>
              <span>v2 actual <b className={pnlCls(g.v2_actual)}>{formatPnl(g.v2_actual)}</b></span>
              <span>plan enforced <b className={pnlCls(g.plan_enforced)}>{formatPnl(g.plan_enforced)}</b></span>
              <span>oi enforced <b className={pnlCls(g.oi_enforced)}>{formatPnl(g.oi_enforced)}</b></span>
              <span>both <b className={pnlCls(g.both_enforced)}>{formatPnl(g.both_enforced)}</b></span>
            </div>
          )}
        </div>
      )}
    </Section>
  );
}

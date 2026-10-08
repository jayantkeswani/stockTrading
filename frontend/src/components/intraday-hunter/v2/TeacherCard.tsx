"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { IhTeacher, IntradayHunterRun } from "@/lib/types";
import { formatPnl } from "@/lib/ihV2";
import { Section, Empty, Val, pnlCls } from "./common";

/** Teacher (@IntradayHunter) plan + live trade for the date, compared with our v2 side. Used by: V2Panel */
export function TeacherCard({ date, run }: { date: string; run: IntradayHunterRun | null }) {
  const [t, setT] = useState<IhTeacher | null>(null);
  const [state, setState] = useState<"loading" | "none" | "ok" | "error">("loading");
  const [err, setErr] = useState<string | null>(null);

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
        const m = e instanceof Error ? e.message : "failed";
        if (/no teacher data/i.test(m)) setState("none");
        else {
          setErr(m);
          setState("error");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [date]);

  const plan = t?.plan;
  const live = t?.live;
  const lastEnter = run?.call2_history?.slice().reverse().find((h) => h.decision === "ENTER");
  const ourSide = run?.direction ?? lastEnter?.direction ?? null;
  const ourDecision = run?.decision ?? run?.status ?? "—";

  return (
    <Section title="Teacher" right={t?.status && <span className="text-[10px] font-mono text-text-muted">{t.status}</span>}>
      {state === "loading" ? (
        <Empty>loading…</Empty>
      ) : state === "none" ? (
        <Empty>no teacher data yet</Empty>
      ) : state === "error" ? (
        <p className="text-[11px] font-mono text-loss">{err}</p>
      ) : (
        <div className="space-y-2 text-[11px] font-mono">
          {t?.errors && t.errors.length > 0 && (
            <div className="border border-warning/30 rounded px-2 py-1 text-warning">
              {t.errors.map((e, i) => <div key={i}>{e.at ? `${e.at} · ` : ""}{e.job ?? ""} {e.error_type ?? ""} — {e.detail ?? ""}</div>)}
            </div>
          )}
          {plan && (
            <div className="space-y-1">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                {([["gap up", plan.gap_up_side], ["flat", plan.flat_side], ["gap down", plan.gap_down_side], ["bias", plan.bias]] as const).map(([k, v]) => (
                  <div key={k} className="border border-border rounded px-2 py-1">
                    <div className="text-text-muted text-[10px] uppercase">{k}</div>
                    <div className="text-text-primary">{v ?? "—"}</div>
                  </div>
                ))}
              </div>
              {plan.summary && <p className="text-text-secondary leading-relaxed">{plan.summary}</p>}
              {plan.levels_onscreen && Object.keys(plan.levels_onscreen).length > 0 && (
                <div>
                  <div className="text-text-muted text-[10px] uppercase">levels on screen</div>
                  {Object.entries(plan.levels_onscreen).map(([idx, lv]) => (
                    <div key={idx}><span className="text-text-secondary">{idx}:</span> {(lv ?? []).join(", ")}</div>
                  ))}
                </div>
              )}
              {plan.levels_audio != null && (
                <div>
                  <div className="text-text-muted text-[10px] uppercase">levels (audio)</div>
                  <Val v={plan.levels_audio} />
                </div>
              )}
            </div>
          )}
          {live && (
            <div className="space-y-1">
              <div className="flex flex-wrap gap-3">
                <span>his side <b className="text-text-primary">{live.side ?? "—"}</b></span>
                <span className="text-text-muted">in {live.entry_clock ?? "—"} · out {live.exit_clock ?? "—"}</span>
                <span className={pnlCls(live.total_pnl)}>total {formatPnl(live.total_pnl)}</span>
              </div>
              {live.legs && live.legs.length > 0 && (
                <table className="w-full text-left">
                  <thead className="text-text-muted text-[10px] uppercase">
                    <tr>{Object.keys(live.legs[0]).map((k) => <th key={k} className="font-normal pr-3">{k}</th>)}</tr>
                  </thead>
                  <tbody>
                    {live.legs.map((l, i) => (
                      <tr key={i} className="border-t border-border/50">
                        {Object.keys(live.legs![0]).map((k) => <td key={k} className="pr-3">{l[k] == null ? "—" : String(l[k])}</td>)}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              <div className="border border-border rounded px-2 py-1">
                <span className="text-text-muted">his trade vs ours: </span>
                <span className="text-text-primary">{live.side ?? "—"}</span>
                <span className="text-text-muted"> vs </span>
                <span className="text-text-primary">{ourSide ?? "—"} ({ourDecision})</span>
                {live.side && ourSide && (
                  <span className={live.side === ourSide ? " text-profit" : " text-warning"}>
                    {live.side === ourSide ? "  aligned" : "  opposed"}
                  </span>
                )}
              </div>
            </div>
          )}
          {!plan && !live && <Empty>teacher record has no plan or live trade</Empty>}
        </div>
      )}
    </Section>
  );
}

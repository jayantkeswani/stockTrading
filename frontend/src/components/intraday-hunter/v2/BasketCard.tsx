"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { IhV2Basket, IhV2Book, IhV2CloseResult } from "@/lib/types";
import { mtmBarPct, formatPnl } from "@/lib/ihV2";
import { DirectionBadge } from "../badges";
import { Section, Empty, Val, pnlCls } from "./common";

const POLL_MS = 3000;
const num = (v: number | null | undefined, d = 2) => (v == null ? "—" : v.toFixed(d));

function MtmBar({ mtm, T, pct }: { mtm: number | null; T: number | null; pct?: number | null }) {
  const pos = mtmBarPct(mtm, T);
  return (
    <div className="space-y-0.5">
      <div className="relative h-2 rounded bg-bg-tertiary">
        <div className="absolute left-1/2 top-0 h-2 w-px bg-border-hover" />
        <div
          className={`absolute top-[-2px] h-3 w-1 rounded ${(mtm ?? 0) >= 0 ? "bg-profit" : "bg-loss"}`}
          style={{ left: `calc(${pos}% - 2px)` }}
        />
      </div>
      <div className="flex justify-between text-[9px] text-text-muted">
        <span>-{T != null ? formatPnl(T).slice(1) : "T"}</span>
        <span className={pnlCls(mtm)}>
          MTM {formatPnl(mtm)}
          {pct != null && ` · ${pct.toFixed(0)}% of T`}
        </span>
        <span>+{T != null ? formatPnl(T).slice(1) : "T"}</span>
      </div>
    </div>
  );
}

function Book({ b }: { b: IhV2Book }) {
  const rh = b.status === "OPEN" ? { active: !!b.round_hold_active, targets: b.round_hold_targets } : null;
  return (
    <div className="border border-border rounded p-2 space-y-1.5 text-[11px] font-mono">
      <div className="flex flex-wrap items-center gap-2">
        <span className={`px-1.5 py-px rounded ${b.is_shadow || b.book === "SHADOW" ? "bg-text-muted/20 text-text-secondary" : "bg-accent/20 text-accent"}`}>
          {b.book}
        </span>
        <DirectionBadge direction={b.direction} />
        <span className={b.status === "OPEN" ? "text-accent" : "text-text-muted"}>{b.status}</span>
        <span className="text-text-muted">cost {num(b.cost, 0)}</span>
        {rh && (
          <span className={`px-1.5 py-px rounded ${rh.active ? "bg-warning/15 text-warning" : "bg-bg-tertiary text-text-muted"}`}>
            round-hold {rh.active ? "active" : "off"}
            {rh.active && rh.targets && Object.keys(rh.targets).length > 0 && <> · <Val v={rh.targets} /></>}
          </span>
        )}
        {b.exit_reason && <span className="ml-auto text-text-secondary">exit: {b.exit_reason}</span>}
      </div>
      <MtmBar mtm={b.mtm} T={b.T} pct={b.pct_of_T} />
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <thead className="text-text-muted text-[10px] uppercase">
            <tr>
              {["Index", "Leg", "Strike", "Entry", "LTP", "Bid", "P&L", "Status"].map((h) => (
                <th key={h} className="font-normal pr-3 py-0.5">{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {b.legs.map((l, i) => (
              <tr key={i} className="border-t border-border/50">
                <td className="pr-3 py-0.5 text-text-primary">{l.index}</td>
                <td className="pr-3">{l.leg ?? "—"}</td>
                <td className="pr-3">{l.strike ?? "—"} {l.option_type ?? ""}</td>
                <td className="pr-3">{num(l.entry_price)}</td>
                <td className="pr-3">{num(l.ltp)}</td>
                <td className="pr-3">{num(l.bid)}</td>
                <td className={`pr-3 ${pnlCls(l.pnl)}`}>{formatPnl(l.pnl)}</td>
                <td className="text-text-secondary">{l.status}{l.exit_reason ? ` · ${l.exit_reason}` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

/** Live v2 basket per book (SHADOW + YOLO profiles), polled every 3s while any book is OPEN; single emergency close. Used by: V2Panel */
export function BasketCard() {
  const [data, setData] = useState<IhV2Basket | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<IhV2CloseResult | null>(null);

  const load = useCallback(async () => {
    try {
      setData(await api.getIhV2Basket());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed to load");
    }
  }, []);

  const anyOpen = !!data?.books.some((b) => b.status === "OPEN");

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!anyOpen) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [anyOpen, load]);

  const realOpen = !!data?.books.some((b) => b.status === "OPEN" && !(b.is_shadow || b.book === "SHADOW"));

  const doClose = async () => {
    setBusy(true);
    try {
      setResult(await api.closeIhV2Basket());
      setConfirming(false);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "close failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Section
      title="Basket"
      right={
        <>
          {data && (
            <span className="text-[10px] font-mono text-text-muted">
              T ±{(data.basket_tp_sl_pct * 100).toFixed(0)}% · time exit {data.time_exit}
            </span>
          )}
          {realOpen && !confirming && (
            <button
              onClick={() => setConfirming(true)}
              className="text-[10px] font-mono px-2 py-0.5 rounded bg-loss text-white hover:opacity-90"
            >
              Close v2 basket
            </button>
          )}
          {confirming && (
            <span className="flex items-center gap-1.5 text-[10px] font-mono">
              <span className="text-loss">close all open YOLO v2 legs?</span>
              <button onClick={doClose} disabled={busy} className="px-2 py-0.5 rounded bg-loss text-white disabled:opacity-50">
                {busy ? "closing…" : "Confirm"}
              </button>
              <button onClick={() => setConfirming(false)} disabled={busy} className="px-2 py-0.5 rounded border border-border text-text-secondary">
                Cancel
              </button>
            </span>
          )}
        </>
      }
    >
      {error && <p className="text-[11px] font-mono text-loss">{error}</p>}
      {result && (
        <div className="text-[11px] font-mono border border-border rounded px-2 py-1 space-y-0.5">
          <div className="text-text-primary">closed {result.closed_legs} leg(s)</div>
          {result.baskets.map((b, i) => (
            <div key={i} className="text-text-secondary">
              {b.book}: MTM <span className={pnlCls(b.mtm)}>{formatPnl(b.mtm)}</span> (T {b.T ?? "—"})
            </div>
          ))}
          <div className="text-text-muted">shadow basket keeps running as the system counterfactual</div>
        </div>
      )}
      {!data || data.books.length === 0 ? (
        <Empty>no v2 basket today</Empty>
      ) : (
        <div className="space-y-2">
          {data.books.map((b) => (
            <Book key={b.book} b={b} />
          ))}
        </div>
      )}
    </Section>
  );
}

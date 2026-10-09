"use client";

import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { IhV2Basket, IhV2Book, IhV2CloseResult } from "@/lib/types";
import { formatPnl, basketTLabel } from "@/lib/ihV2";
import { DirectionBadge } from "../intraday-hunter/badges";
import { MtmBar } from "../intraday-hunter/v2/BasketCard";
import { Section, Empty, pnlCls } from "../intraday-hunter/v2/common";

const POLL_MS = 5000;
const num = (v: number | null | undefined, d = 2) => (v == null ? "—" : v.toFixed(d));

function MobileBook({ b }: { b: IhV2Book }) {
  const shadow = b.is_shadow || b.book === "SHADOW";
  return (
    <div className="border border-border rounded p-2 space-y-1.5 text-[11px] font-mono">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className={`px-1.5 py-px rounded ${shadow ? "bg-text-muted/20 text-text-secondary" : "bg-accent/20 text-accent"}`}>{b.book}</span>
        <DirectionBadge direction={b.direction} />
        <span className={b.status === "OPEN" ? "text-accent" : "text-text-muted"}>{b.status}</span>
        <span className="text-text-muted ml-auto">cost {num(b.cost, 0)}</span>
      </div>
      <MtmBar mtm={b.mtm} T={b.T} pct={b.pct_of_T} />
      {b.exit_reason && <div className="text-text-secondary">exit: {b.exit_reason}</div>}
      <div className="space-y-1">
        {b.legs.map((l, i) => (
          <div key={i} className="border-t border-border/50 pt-1 grid grid-cols-[1fr_auto] gap-x-2 gap-y-0.5">
            <span className="text-text-primary truncate">
              {l.index} {l.leg ?? ""} {l.strike ?? "—"} {l.option_type ?? ""}
            </span>
            <span className={`text-right ${pnlCls(l.pnl)}`}>{formatPnl(l.pnl)}</span>
            <span className="text-text-muted">
              in {num(l.entry_price)} · ltp {num(l.ltp)}
            </span>
            <span className="text-text-secondary text-right">
              {l.status}
              {l.exit_reason ? ` · ${l.exit_reason}` : ""}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

/**
 * Phone v2 basket: per-book MTM bar + stacked leg rows and a two-step emergency close
 * (Close → inline Confirm/Cancel) via api.closeIhV2Basket (non-shadow books only).
 * Polls every 5s while any book is OPEN and on `refreshKey`. Used by: MobileHunterV2
 */
export function MobileV2Basket({ refreshKey }: { refreshKey?: number }) {
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
  const realOpen = !!data?.books.some((b) => b.status === "OPEN" && !(b.is_shadow || b.book === "SHADOW"));

  useEffect(() => {
    const first = setTimeout(load, 0);
    return () => clearTimeout(first);
  }, [load, refreshKey]);

  useEffect(() => {
    if (!anyOpen) return;
    const t = setInterval(load, POLL_MS);
    return () => clearInterval(t);
  }, [anyOpen, load]);

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
      right={data && <span className="text-[10px] font-mono text-text-muted">{basketTLabel(data)} · exit {data.time_exit}</span>}
    >
      {error && <p className="text-[11px] font-mono text-loss">{error}</p>}
      {realOpen && !confirming && (
        <button onClick={() => setConfirming(true)} className="w-full py-2 rounded bg-loss text-white text-[12px] font-mono font-medium">
          EMERGENCY CLOSE v2 basket
        </button>
      )}
      {confirming && (
        <div className="border border-loss/50 rounded p-2 space-y-2">
          <p className="text-[11px] font-mono text-loss">Close all open YOLO v2 legs now? This cannot be undone.</p>
          <div className="grid grid-cols-2 gap-2">
            <button onClick={doClose} disabled={busy} className="py-2 rounded bg-loss text-white text-[12px] font-mono disabled:opacity-50">
              {busy ? "closing…" : "Confirm close"}
            </button>
            <button onClick={() => setConfirming(false)} disabled={busy} className="py-2 rounded border border-border text-text-secondary text-[12px] font-mono">
              Cancel
            </button>
          </div>
        </div>
      )}
      {result && (
        <div className="text-[11px] font-mono border border-border rounded px-2 py-1 space-y-0.5">
          <div className="text-text-primary">closed {result.closed_legs} leg(s)</div>
          {result.baskets.map((b, i) => (
            <div key={i} className="text-text-secondary">
              {b.book}: MTM <span className={pnlCls(b.mtm)}>{formatPnl(b.mtm)}</span>
            </div>
          ))}
        </div>
      )}
      {!data || data.books.length === 0 ? (
        <Empty>no v2 basket today</Empty>
      ) : (
        <div className="space-y-2">
          {data.books.map((b) => (
            <MobileBook key={b.book} b={b} />
          ))}
        </div>
      )}
    </Section>
  );
}

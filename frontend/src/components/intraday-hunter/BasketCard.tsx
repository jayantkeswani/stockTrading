"use client";

import { useMemo, useState } from "react";
import type { IntradayHunterBasketLeg } from "@/lib/types";
import { api } from "@/lib/api";
import { formatINR, pnlColor } from "@/lib/formatters";

const SECTION_HDR = "text-xs font-mono font-medium text-text-secondary uppercase tracking-wider";

/** True when a leg can be manually closed (live, real-book — shadow legs are read-only). */
function isClosable(leg: IntradayHunterBasketLeg): leg is IntradayHunterBasketLeg & { position_id: string } {
  return leg.status === "OPEN" && !leg.is_shadow && !!leg.position_id;
}

/**
 * Live Intraday Hunter basket + exits. Each leg's own option-premium SL/target closes it
 * individually (the normal per-position check); the index spot vs index_sl/index_target shown
 * here is informational context, not what triggers the exit. Open real-book legs carry a
 * checkbox + select-all + "Close selected" for a manual whole-basket close.
 */
export function BasketCard({
  legs,
  onClosed,
}: {
  legs: IntradayHunterBasketLeg[];
  onClosed: () => void;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  const closableIds = useMemo(
    () => legs.filter(isClosable).map((l) => l.position_id),
    [legs]
  );
  const allSelected = closableIds.length > 0 && closableIds.every((id) => selected.has(id));

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const toggleAll = () =>
    setSelected(allSelected ? new Set() : new Set(closableIds));

  const closeSelected = async () => {
    const ids = closableIds.filter((id) => selected.has(id));
    if (ids.length === 0) return;
    setBusy(true);
    try {
      await Promise.all(ids.map((id) => api.closePosition(id, "MANUAL")));
      setSelected(new Set());
      onClosed();
    } finally {
      setBusy(false);
    }
  };

  if (legs.length === 0) return null;

  const selectedCount = closableIds.filter((id) => selected.has(id)).length;

  return (
    <div className="bg-bg-secondary border border-border rounded p-3 space-y-2">
      <div className="flex items-center gap-2">
        <h2 className={SECTION_HDR}>Basket &amp; Exits</h2>
        <span className="text-[10px] font-mono text-text-muted">
          per-leg premium SL/target · select legs to close manually
        </span>
        <div className="flex-1" />
        {closableIds.length > 0 && (
          <button
            onClick={closeSelected}
            disabled={busy || selectedCount === 0}
            className="text-[10px] font-mono px-2 py-0.5 rounded border border-loss/40 text-loss hover:bg-loss/10 disabled:opacity-40"
          >
            {busy ? "closing…" : `Close selected${selectedCount ? ` (${selectedCount})` : ""}`}
          </button>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-[11px] font-mono">
          <thead>
            <tr className="text-text-muted text-left border-b border-border">
              <th className="px-1.5 py-1 w-6">
                {closableIds.length > 0 && (
                  <input
                    type="checkbox"
                    checked={allSelected}
                    onChange={toggleAll}
                    aria-label="select all"
                    className="accent-accent"
                  />
                )}
              </th>
              <th className="px-1.5 py-1">Book</th>
              <th className="px-1.5 py-1">Leg</th>
              <th className="px-1.5 py-1 text-right">Entry</th>
              <th className="px-1.5 py-1 text-right">LTP</th>
              <th className="px-1.5 py-1 text-right">P&amp;L</th>
              <th className="px-1.5 py-1">Index spot vs SL / Target</th>
              <th className="px-1.5 py-1">Status</th>
            </tr>
          </thead>
          <tbody>
            {legs.map((leg, i) => {
              const closable = isClosable(leg);
              const pnl = leg.status === "OPEN" ? leg.unrealized_pnl : leg.realized_pnl;
              return (
                <tr key={leg.position_id || leg.trade_id || i} className="border-b border-border/50">
                  <td className="px-1.5 py-1">
                    {closable && (
                      <input
                        type="checkbox"
                        checked={selected.has(leg.position_id)}
                        onChange={() => toggle(leg.position_id)}
                        aria-label={`select ${leg.index}`}
                        className="accent-accent"
                      />
                    )}
                  </td>
                  <td className="px-1.5 py-1">
                    <span
                      className={`px-1 py-px rounded text-[10px] ${
                        leg.is_shadow
                          ? "bg-shadowbook-bg/15 text-shadowbook"
                          : "bg-accent/10 text-accent"
                      }`}
                    >
                      {leg.book}
                    </span>
                  </td>
                  <td className="px-1.5 py-1 text-text-primary">
                    {leg.index}
                    {leg.strike != null && <span className="text-text-secondary"> {leg.strike.toLocaleString("en-IN")}</span>}
                    {leg.option_type && (
                      <span className={leg.option_type === "CE" ? "text-profit" : "text-loss"}> {leg.option_type}</span>
                    )}
                    {leg.itm_depth != null && leg.itm_depth > 0 && (
                      <span className="text-text-muted"> ITM-{leg.itm_depth}</span>
                    )}
                  </td>
                  <td className="px-1.5 py-1 text-right text-text-secondary">
                    {leg.entry_price != null ? formatINR(leg.entry_price) : "—"}
                  </td>
                  <td className="px-1.5 py-1 text-right text-text-primary">
                    {leg.current_price != null ? formatINR(leg.current_price) : "—"}
                  </td>
                  <td className={`px-1.5 py-1 text-right ${pnlColor(pnl ?? 0)}`}>
                    {pnl != null ? formatINR(pnl) : "—"}
                  </td>
                  <td className="px-1.5 py-1 text-text-muted">
                    {leg.status === "OPEN" ? (
                      <>
                        <span className="text-text-primary">
                          {leg.index_spot != null ? leg.index_spot.toLocaleString("en-IN") : "—"}
                        </span>
                        <span className="text-loss/70">
                          {" "}SL {leg.index_sl != null ? leg.index_sl.toLocaleString("en-IN") : "—"}
                        </span>
                        <span className="text-profit/70">
                          {" "}T {leg.index_target != null ? leg.index_target.toLocaleString("en-IN") : "—"}
                        </span>
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td className="px-1.5 py-1">
                    {leg.status === "OPEN" ? (
                      <span className="text-profit">OPEN</span>
                    ) : (
                      <span className="text-text-muted">{leg.exit_reason || "CLOSED"}</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

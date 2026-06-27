"use client";

import type { IntradayHunterHistoryItem } from "@/lib/types";
import { StatusChip, DirectionBadge, confidenceColor } from "./badges";

const SECTION_HDR = "text-xs font-mono font-medium text-text-secondary uppercase tracking-wider";

/**
 * Prior-day timeline: decision, direction, confidence, trapped side, outcome + human P&L note.
 * When `onSelect` is provided each row becomes a button that opens the full-day detail popup.
 */
export function HistoryTimeline({
  items,
  onSelect,
}: {
  items: IntradayHunterHistoryItem[];
  onSelect?: (date: string) => void;
}) {
  return (
    <div className="bg-bg-secondary border border-border rounded p-3 space-y-2">
      <h2 className={SECTION_HDR}>History</h2>
      {items.length === 0 ? (
        <p className="text-xs font-mono text-text-muted lowercase">no prior runs</p>
      ) : (
        <div className="space-y-1.5 max-h-[calc(100vh-160px)] overflow-y-auto">
          {items.map((it) => {
            const rowCls = onSelect
              ? "w-full text-left border border-border rounded px-2 py-1.5 space-y-1 hover:border-border-hover hover:bg-bg-tertiary/40 transition-colors cursor-pointer"
              : "border border-border rounded px-2 py-1.5 space-y-1";
            const rowContent = (
              <>
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-text-primary">{it.trading_date}</span>
                {it.is_expiry && (
                  <span className="text-[9px] font-mono px-1 py-px rounded bg-warning/15 text-warning">
                    {it.expiry_index} EXP
                  </span>
                )}
                <div className="ml-auto flex items-center gap-1.5">
                  <DirectionBadge direction={it.direction} />
                  {it.confidence != null && (
                    <span className={`text-[10px] font-mono ${confidenceColor(it.confidence)}`}>{it.confidence}</span>
                  )}
                  <StatusChip status={it.decision || it.status} />
                </div>
              </div>
              {it.thesis && (
                <p className="text-[11px] font-mono text-text-secondary line-clamp-2">{it.thesis}</p>
              )}
              <div className="flex items-center gap-2 text-[10px] font-mono text-text-muted">
                {it.trapped_side && <span>trapped: {it.trapped_side}</span>}
                {it.outcome_played_out != null && (
                  <span className={it.outcome_played_out ? "text-profit" : "text-loss"}>
                    {it.outcome_played_out ? "✓ played out" : "✗ failed"}
                  </span>
                )}
              </div>
              {it.realized_outcome_note && (
                <p className="text-[10px] font-mono text-text-secondary italic">{it.realized_outcome_note}</p>
              )}
              </>
            );
            return onSelect ? (
              <button
                key={it.trading_date}
                type="button"
                onClick={() => onSelect(it.trading_date)}
                className={rowCls}
              >
                {rowContent}
              </button>
            ) : (
              <div key={it.trading_date} className={rowCls}>
                {rowContent}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

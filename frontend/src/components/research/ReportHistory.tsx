"use client";

import { useState } from "react";
import type { ResearchReportListItem } from "@/lib/types";

const REC_COLORS: Record<string, string> = {
  BUY: "text-[#00e68a]",
  HOLD: "text-accent",
  SELL: "text-[#ff4060]",
  AVOID: "text-[#ff4060]",
};

export function ReportHistory({
  reports,
  selectedId,
  onSelect,
  onDelete,
}: {
  reports: ResearchReportListItem[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}) {
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  if (reports.length === 0) {
    return (
      <div className="text-xs font-mono text-text-muted py-2">
        no past reports
      </div>
    );
  }

  const handleDeleteClick = (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (confirmDeleteId === id) {
      // Second click — confirm delete
      onDelete(id);
      setConfirmDeleteId(null);
    } else {
      // First click — show confirmation
      setConfirmDeleteId(id);
      // Auto-reset after 3 seconds
      setTimeout(() => setConfirmDeleteId((prev) => (prev === id ? null : prev)), 3000);
    }
  };

  return (
    <div className="space-y-0.5 max-h-[200px] overflow-y-auto">
      {reports.map((r) => (
        <div
          key={r.id}
          className={`flex items-center justify-between px-2.5 py-1.5 rounded text-xs font-mono transition-colors cursor-pointer ${
            selectedId === r.id
              ? "bg-accent/10 text-accent"
              : "hover:bg-bg-tertiary text-text-secondary"
          }`}
          onClick={() => onSelect(r.id)}
        >
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-text-primary font-medium">{r.symbol}</span>
            {r.recommendation && (
              <span className={`text-[10px] font-bold ${REC_COLORS[r.recommendation] || "text-text-muted"}`}>
                {r.recommendation}
              </span>
            )}
            {r.confidence_score !== null && (
              <span className="text-text-muted text-[10px]">
                ({r.confidence_score.toFixed(0)})
              </span>
            )}
          </div>

          <div className="flex items-center gap-2 flex-shrink-0">
            <span className="text-[10px] text-text-muted">
              {_timeAgo(r.created_at)}
            </span>
            <button
              onClick={(e) => handleDeleteClick(e, r.id)}
              className={`text-[10px] font-mono px-1.5 py-0.5 rounded transition-colors ${
                confirmDeleteId === r.id
                  ? "bg-[#ff4060]/20 text-[#ff4060] font-medium"
                  : "text-text-muted hover:text-[#ff4060] hover:bg-[#ff4060]/10"
              }`}
              title={confirmDeleteId === r.id ? "Click again to confirm" : "Delete report"}
            >
              {confirmDeleteId === r.id ? "confirm?" : "×"}
            </button>
          </div>
        </div>
      ))}
    </div>
  );
}

function _timeAgo(dateStr: string): string {
  const now = Date.now();
  const then = new Date(dateStr).getTime();
  const diff = now - then;
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

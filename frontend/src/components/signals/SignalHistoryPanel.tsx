"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { SignalHistory } from "@/lib/types";

function formatHistoryTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString("en-IN", {
      timeZone: "Asia/Kolkata",
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
    });
  } catch {
    return iso;
  }
}

function fmtPrice(v: number): string {
  return "₹" + v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function SignalHistoryPanel({ signalId }: { signalId: string }) {
  const [history, setHistory] = useState<SignalHistory[] | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (history !== null || loading) return;
    setLoading(true);
    api.getSignalHistory(signalId)
      .then(setHistory)
      .catch(() => setHistory([]))
      .finally(() => setLoading(false));
  }, [signalId, history, loading]);

  if (loading) {
    return (
      <p className="text-[9px] font-mono text-text-muted mt-2">loading history…</p>
    );
  }

  if (!history || history.length === 0) return null;

  // history is version DESC — latest snapshot first
  return (
    <div className="mt-3 pt-2 border-t border-border/30">
      <p className="text-[9px] font-mono text-text-muted uppercase tracking-wider mb-2">
        Signal History — {history.length} version{history.length > 1 ? "s" : ""}
      </p>

      <div className="divide-y divide-border/20">
        {history.map((h) => {
          const confNum = h.confidence != null ? Number(h.confidence) : null;
          const confColor =
            confNum == null
              ? "text-text-muted"
              : confNum >= 70
              ? "text-profit"
              : confNum >= 55
              ? "text-accent"
              : "text-loss";

          return (
            <div key={h.id} className="py-1.5 first:pt-0">
              {/* Metrics row */}
              <div className="flex items-center gap-x-3 gap-y-0.5 text-[10px] font-mono flex-wrap">
                {/* version badge */}
                <span className="text-text-muted w-5 shrink-0">v{h.version}</span>

                {/* time */}
                <span className="text-text-muted shrink-0">
                  {formatHistoryTime(h.captured_at)}
                </span>

                {/* entry */}
                <span className="shrink-0">
                  <span className="text-text-muted">Entry </span>
                  <span className="text-text-secondary">{fmtPrice(h.entry_price)}</span>
                </span>

                {/* SL */}
                <span className="shrink-0">
                  <span className="text-text-muted">SL </span>
                  <span className="text-loss">{fmtPrice(h.stop_loss)}</span>
                </span>

                {/* target */}
                {h.target_price != null && (
                  <span className="shrink-0">
                    <span className="text-text-muted">Tgt </span>
                    <span className="text-profit">{fmtPrice(h.target_price)}</span>
                  </span>
                )}

                {/* confidence */}
                {confNum != null && (
                  <span className={`shrink-0 ${confColor}`}>
                    {confNum.toFixed(1)}%
                  </span>
                )}

                {/* AI adjustment */}
                {h.ai_adjustment != null && h.ai_adjustment !== 0 && (
                  <span className={`text-[9px] shrink-0 ${h.ai_adjustment > 0 ? "text-profit" : "text-loss"}`}>
                    AI {h.ai_adjustment > 0 ? "+" : ""}{h.ai_adjustment}
                  </span>
                )}

                {/* AI action badge */}
                {h.ai_action && h.ai_action !== "PROCEED" && (
                  <span className={`text-[9px] px-1 py-px rounded border shrink-0 ${
                    h.ai_action === "SKIP"
                      ? "bg-loss/10 text-loss border-loss/30"
                      : "bg-warning/10 text-warning border-warning/30"
                  }`}>
                    {h.ai_action}
                  </span>
                )}

                {/* executable badge */}
                {!h.executable && (
                  <span className="text-[9px] text-text-muted italic shrink-0">blocked</span>
                )}
              </div>

              {/* AI summary — second line */}
              {h.ai_summary ? (
                <p className="text-[9px] font-mono text-text-muted pl-7 mt-0.5 leading-relaxed">
                  {h.ai_summary}
                </p>
              ) : (
                <p className="text-[9px] font-mono text-text-muted/50 pl-7 mt-0.5 italic">
                  no AI analysis at this version
                </p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

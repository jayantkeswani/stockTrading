"use client";

import { useStore } from "@/store";
import { formatINR, formatTime } from "@/lib/formatters";
import { STRATEGY_LABELS } from "@/lib/constants";
import { api } from "@/lib/api";

export function SignalFeed() {
  const { signals } = useStore();
  const pendingSignals = signals.filter((s) => s.status === "PENDING");

  const handleExecute = async (signalId: string) => {
    try {
      await api.executeSignal(signalId);
    } catch (err) {
      console.error("Failed to execute signal:", err);
    }
  };

  const handleReject = async (signalId: string) => {
    try {
      await api.rejectSignal(signalId);
    } catch (err) {
      console.error("Failed to reject signal:", err);
    }
  };

  return (
    <div className="rounded-lg border border-border bg-bg-secondary">
      <div className="px-4 py-3 border-b border-border">
        <h2 className="text-sm font-semibold text-text-primary">
          Signals
          {pendingSignals.length > 0 && (
            <span className="ml-2 text-xs px-1.5 py-0.5 rounded-full bg-accent/20 text-accent">
              {pendingSignals.length}
            </span>
          )}
        </h2>
      </div>

      {pendingSignals.length === 0 ? (
        <div className="p-6 text-center text-text-muted text-sm">
          No active signals
        </div>
      ) : (
        <div className="divide-y divide-border/50 max-h-[300px] overflow-y-auto">
          {pendingSignals.map((signal) => (
            <div key={signal.id} className="p-3 hover:bg-bg-tertiary/50 transition-colors">
              <div className="flex items-center justify-between mb-1">
                <div className="flex items-center gap-2">
                  <span
                    className={`text-xs font-bold ${
                      signal.signal_type === "BUY_CE" ? "text-profit" : "text-loss"
                    }`}
                  >
                    {signal.signal_type === "BUY_CE" ? "▲ CE" : "▼ PE"}
                  </span>
                  <span className="text-sm font-medium">{signal.symbol}</span>
                </div>
                {signal.confidence && (
                  <span className="text-xs text-text-muted">
                    {signal.confidence}% conf
                  </span>
                )}
              </div>

              <div className="text-xs text-text-secondary mb-2 line-clamp-2">
                {signal.reason}
              </div>

              <div className="flex items-center justify-between">
                <div className="flex gap-2 text-xs font-mono">
                  <span className="text-text-muted">
                    Entry: {formatINR(signal.entry_price)}
                  </span>
                  <span className="text-loss">SL: {formatINR(signal.stop_loss)}</span>
                </div>
                <div className="flex gap-1">
                  <button
                    onClick={() => handleExecute(signal.id)}
                    className="text-xs px-2 py-1 rounded bg-profit/20 text-profit hover:bg-profit/30 transition-colors"
                  >
                    Execute
                  </button>
                  <button
                    onClick={() => handleReject(signal.id)}
                    className="text-xs px-2 py-1 rounded bg-bg-tertiary text-text-muted hover:text-text-secondary transition-colors"
                  >
                    Dismiss
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

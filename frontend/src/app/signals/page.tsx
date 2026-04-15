"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatINR, formatTime, formatDate } from "@/lib/formatters";
import { STRATEGY_LABELS, STATUS_COLORS } from "@/lib/constants";
import type { Signal } from "@/lib/types";

export default function SignalsPage() {
  const [signals, setSignals] = useState<Signal[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const data = (await api.getSignals()) as Signal[];
        setSignals(data);
      } catch {
        // API not running yet
      }
      setLoading(false);
    }
    load();
  }, []);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-semibold">Signal History</h1>

      <div className="rounded-lg border border-border bg-bg-secondary overflow-hidden">
        {loading ? (
          <div className="p-8 text-center text-text-muted">Loading...</div>
        ) : signals.length === 0 ? (
          <div className="p-8 text-center text-text-muted">No signals generated yet</div>
        ) : (
          <div className="divide-y divide-border/50">
            {signals.map((signal) => (
              <div key={signal.id} className="p-4 hover:bg-bg-tertiary/30">
                <div className="flex items-center justify-between mb-2">
                  <div className="flex items-center gap-3">
                    <span
                      className={`text-sm font-bold ${
                        signal.signal_type === "BUY_CE" ? "text-profit" : "text-loss"
                      }`}
                    >
                      {signal.signal_type === "BUY_CE" ? "▲ BUY CE" : "▼ BUY PE"}
                    </span>
                    <span className="font-medium">{signal.symbol}</span>
                    <span className="text-xs text-text-muted">
                      {signal.strike_price} | {signal.expiry_date}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-xs px-1.5 py-0.5 rounded ${
                        STATUS_COLORS[signal.status] || ""
                      }`}
                    >
                      {signal.status}
                    </span>
                    <span className="text-xs text-text-muted">
                      {formatDate(signal.generated_at)} {formatTime(signal.generated_at)}
                    </span>
                  </div>
                </div>
                <p className="text-xs text-text-secondary">{signal.reason}</p>
                <div className="mt-2 flex gap-4 text-xs font-mono text-text-muted">
                  <span>Entry: {formatINR(signal.entry_price)}</span>
                  <span>SL: {formatINR(signal.stop_loss)}</span>
                  {signal.target_price && (
                    <span>Target: {formatINR(signal.target_price)}</span>
                  )}
                  {signal.confidence && (
                    <span>Confidence: {signal.confidence}%</span>
                  )}
                  <span>
                    Strategy:{" "}
                    {STRATEGY_LABELS[signal.strategy_name] || signal.strategy_name}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

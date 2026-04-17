"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatINR, formatTime, formatDate } from "@/lib/formatters";
import { STRATEGY_LABELS, STATUS_COLORS } from "@/lib/constants";
import type { Signal } from "@/lib/types";

function SignalTypeBadge({ signalType }: { signalType: Signal["signal_type"] }) {
  const map: Record<string, { label: string; cls: string }> = {
    BUY_CE: { label: "\u25B2 CE", cls: "text-profit" },
    BUY_PE: { label: "\u25BC PE", cls: "text-loss" },
    BUY_FUT: { label: "\u25B2 FUT", cls: "text-accent" },
    SELL_FUT: { label: "\u25BC FUT", cls: "text-loss" },
  };
  const entry = map[signalType] || { label: signalType, cls: "text-text-muted" };
  return <span className={`text-xs font-mono font-bold ${entry.cls}`}>{entry.label}</span>;
}

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
    <div className="space-y-2">
      <h1 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
        Signal History
      </h1>

      <div className="rounded border border-border bg-bg-secondary overflow-hidden">
        {loading ? (
          <div className="px-3 py-6 text-center text-text-muted text-xs font-mono">loading...</div>
        ) : signals.length === 0 ? (
          <div className="px-3 py-6 text-center text-text-muted text-xs font-mono">no signals generated yet</div>
        ) : (
          <div className="divide-y divide-border/30">
            {signals.map((signal) => (
              <div key={signal.id} className="px-3 py-2 hover:bg-bg-tertiary/30">
                <div className="flex items-center justify-between mb-1">
                  <div className="flex items-center gap-2">
                    <SignalTypeBadge signalType={signal.signal_type} />
                    <span className="text-xs font-mono font-medium">{signal.symbol}</span>
                    {signal.instrument_type === "OPTION" && signal.executable && (
                      <span className="text-[10px] font-mono text-text-muted">
                        {signal.strike_price} | {signal.expiry_date}
                      </span>
                    )}
                    {signal.instrument_type === "FUTURE" && (
                      <span className="text-[10px] font-mono text-text-muted">FUT | {signal.expiry_date}</span>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-[10px] font-mono px-1 py-px rounded ${
                        STATUS_COLORS[signal.status] || ""
                      }`}
                    >
                      {signal.status}
                    </span>
                    <span className="text-[10px] font-mono text-text-muted">
                      {formatDate(signal.generated_at)} {formatTime(signal.generated_at)}
                    </span>
                  </div>
                </div>
                <p className="text-xs font-mono text-text-muted leading-relaxed">{signal.reason}</p>
                <div className="mt-1 flex gap-3 text-xs font-mono text-text-muted flex-wrap">
                  {signal.instrument_type === "OPTION" && !signal.executable ? (
                    <>
                      <span className="text-warning">Idx: {formatINR(signal.entry_price)}</span>
                      <span className="italic">premium n/a</span>
                    </>
                  ) : (
                    <span>Entry: {formatINR(signal.entry_price)}</span>
                  )}
                  {signal.stop_loss > 0 && <span>SL: {formatINR(signal.stop_loss)}</span>}
                  {signal.target_price && signal.target_price > 0 && (
                    <span>Tgt: {formatINR(signal.target_price)}</span>
                  )}
                  {signal.confidence && <span>{signal.confidence}%</span>}
                  <span className="text-accent">
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

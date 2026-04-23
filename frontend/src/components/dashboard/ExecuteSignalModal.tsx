"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { formatINR } from "@/lib/formatters";
import type { Signal, SignalPreview } from "@/lib/types";
import { STRATEGY_LABELS } from "@/lib/constants";

interface Props {
  signal: Signal;
  onClose: () => void;
  onExecuted: () => void;
}

export function ExecuteSignalModal({ signal, onClose, onExecuted }: Props) {
  const [preview, setPreview] = useState<SignalPreview | null>(null);
  const [lotsOverride, setLotsOverride] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [executing, setExecuting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const isCE = signal.signal_type === "BUY_CE";
  const isFuture = signal.instrument_type === "FUTURE";
  const directionColor = isCE || isFuture ? "text-profit" : "text-loss";
  const symbolLabel = isFuture
    ? `${signal.symbol} FUT`
    : `${signal.symbol} ${signal.strike_price > 0 ? signal.strike_price : ""} ${isCE ? "CE" : "PE"}`;

  useEffect(() => {
    let cancelled = false;
    api
      .previewSignal(signal.id)
      .then((p) => {
        if (!cancelled) {
          setPreview(p);
          setLotsOverride(p.lots);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Failed to load preview");
          setLoading(false);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [signal.id]);

  const effectiveLots = lotsOverride ?? preview?.lots ?? 1;
  const lotSize = preview?.lot_size ?? 1;
  const quantity = effectiveLots * lotSize;
  const entryPrice = preview?.entry_price ?? signal.entry_price;
  const sl = preview?.stop_loss ?? signal.stop_loss;
  const target = preview?.target_price ?? signal.target_price;
  const capitalAtRisk = Math.abs(entryPrice - sl) * quantity;

  const handleExecute = async () => {
    setExecuting(true);
    setError(null);
    try {
      await api.executeSignal(signal.id, {
        lots: lotsOverride !== preview?.lots ? effectiveLots : undefined,
      });
      onExecuted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Execution failed");
      setExecuting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center" onClick={onClose}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" />
      <div
        className="relative w-[420px] rounded border border-border bg-bg-elevated shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-4 py-2.5 border-b border-border flex items-center justify-between">
          <div className="flex items-center gap-2">
            <span className={`text-sm font-bold ${directionColor}`}>
              {isCE ? "▲" : isFuture ? "▲" : "▼"}
            </span>
            <span className="text-sm font-mono font-medium text-text-primary">
              {symbolLabel}
            </span>
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-accent/10 text-accent">
              {STRATEGY_LABELS[signal.strategy_name] || signal.strategy_name}
            </span>
          </div>
          <button
            onClick={onClose}
            className="text-text-muted hover:text-text-primary text-sm font-mono"
          >
            ✕
          </button>
        </div>

        {loading ? (
          <div className="px-4 py-6 text-center text-xs font-mono text-text-muted">
            fetching live price...
          </div>
        ) : error && !preview ? (
          <div className="px-4 py-4">
            <p className="text-xs font-mono text-loss">{error}</p>
            <button
              onClick={onClose}
              className="mt-3 text-xs font-mono px-3 py-1.5 rounded border border-border text-text-secondary hover:text-text-primary"
            >
              Close
            </button>
          </div>
        ) : (
          <>
            {/* Price grid */}
            <div className="px-4 py-3 grid grid-cols-3 gap-3 border-b border-border/50">
              <div>
                <p className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-0.5">Entry (live)</p>
                <p className="text-sm font-mono text-text-primary">{formatINR(entryPrice)}</p>
              </div>
              <div>
                <p className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-0.5">SL</p>
                <p className="text-sm font-mono text-loss">{formatINR(sl)}</p>
              </div>
              <div>
                <p className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-0.5">Target</p>
                <p className="text-sm font-mono text-profit">
                  {target ? formatINR(target) : "—"}
                </p>
              </div>
            </div>

            {/* Lot size input + capital at risk */}
            <div className="px-4 py-3 border-b border-border/50">
              <div className="flex items-center justify-between mb-2">
                <label className="text-[10px] font-mono text-text-muted uppercase tracking-wider">
                  Lots
                </label>
                <div className="flex items-center gap-1.5">
                  <button
                    onClick={() => setLotsOverride(Math.max(1, effectiveLots - 1))}
                    className="w-5 h-5 rounded border border-border text-text-secondary hover:text-text-primary font-mono text-xs flex items-center justify-center"
                  >
                    −
                  </button>
                  <input
                    type="number"
                    min={1}
                    value={effectiveLots}
                    onChange={(e) => setLotsOverride(Math.max(1, parseInt(e.target.value) || 1))}
                    className="w-14 text-center text-sm font-mono bg-bg-secondary border border-border rounded px-1 py-0.5 text-text-primary focus:outline-none focus:border-accent"
                  />
                  <button
                    onClick={() => setLotsOverride(effectiveLots + 1)}
                    className="w-5 h-5 rounded border border-border text-text-secondary hover:text-text-primary font-mono text-xs flex items-center justify-center"
                  >
                    +
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-3 gap-3 text-xs font-mono">
                <div>
                  <span className="text-text-muted">Qty </span>
                  <span className="text-text-secondary">{quantity}</span>
                </div>
                <div>
                  <span className="text-text-muted">Lot size </span>
                  <span className="text-text-secondary">{lotSize}</span>
                </div>
                <div>
                  <span className="text-text-muted">Risk </span>
                  <span className="text-loss">{formatINR(capitalAtRisk)}</span>
                </div>
              </div>

              {lotsOverride !== preview?.lots && preview && (
                <p className="mt-1.5 text-[10px] font-mono text-accent">
                  Computed: {preview.lots} lots — overridden to {effectiveLots}
                </p>
              )}
            </div>

            {error && (
              <div className="px-4 py-2 bg-loss/10">
                <p className="text-xs font-mono text-loss">{error}</p>
              </div>
            )}

            {/* Actions */}
            <div className="px-4 py-3 flex items-center gap-2 justify-end">
              <button
                onClick={onClose}
                disabled={executing}
                className="text-xs font-mono px-3 py-1.5 rounded border border-border text-text-secondary hover:text-text-primary disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                onClick={handleExecute}
                disabled={executing}
                className="text-xs font-mono font-medium px-4 py-1.5 rounded bg-profit/20 text-profit hover:bg-profit/30 border border-profit/30 disabled:opacity-50 transition-colors"
              >
                {executing ? "Placing..." : `Execute ${effectiveLots}L × ${lotSize}`}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

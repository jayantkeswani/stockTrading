"use client";

import { useState } from "react";
import { useStore } from "@/store";
import { formatINR } from "@/lib/formatters";
import { STRATEGY_LABELS } from "@/lib/constants";
import { api } from "@/lib/api";
import type { Signal } from "@/lib/types";

export function ScannerPanel() {
  const { signals, updateSignal, removeSignal } = useStore();
  const pendingSignals = signals.filter((s) => s.status === "PENDING");

  const handleExecute = async (signalId: string) => {
    try {
      await api.executeSignal(signalId);
      updateSignal(signalId, { status: "EXECUTED" });
    } catch (err) {
      console.error("Failed to execute signal:", err);
    }
  };

  const handleReject = async (signalId: string) => {
    try {
      await api.rejectSignal(signalId);
      removeSignal(signalId);
    } catch (err) {
      console.error("Failed to reject signal:", err);
    }
  };

  return (
    <div className="rounded border border-border bg-bg-secondary">
      <div className="px-3 py-1.5 border-b border-border flex items-center justify-between">
        <div className="flex items-center gap-2">
          <h2 className="text-[11px] font-mono font-medium text-text-secondary uppercase tracking-wider">
            Scanner
          </h2>
          {pendingSignals.length > 0 && (
            <span className="text-[10px] font-mono px-1 py-px rounded bg-accent/15 text-accent">
              {pendingSignals.length}
            </span>
          )}
        </div>
        <div className="flex items-center gap-1">
          <div className="w-1 h-1 rounded-full bg-profit animate-pulse" />
          <span className="text-[9px] text-text-muted font-mono">LIVE</span>
        </div>
      </div>

      {pendingSignals.length === 0 ? (
        <div className="px-3 py-6 text-center">
          <p className="text-[11px] text-text-muted font-mono">waiting for signals...</p>
        </div>
      ) : (
        <div className="max-h-[340px] overflow-y-auto">
          {pendingSignals.map((signal) => (
            <SignalRow
              key={signal.id}
              signal={signal}
              onExecute={handleExecute}
              onDismiss={handleReject}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function SignalRow({
  signal,
  onExecute,
  onDismiss,
}: {
  signal: Signal;
  onExecute: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const isFuture = signal.instrument_type === "FUTURE";
  const isCE = signal.signal_type === "BUY_CE";
  const isUnresolvedOption = signal.instrument_type === "OPTION" && !signal.executable;

  const risk = signal.entry_price - signal.stop_loss;
  const reward = signal.target_price ? signal.target_price - signal.entry_price : 0;
  const rrRatio = risk > 0 && reward > 0 ? (reward / risk).toFixed(1) : "?";

  const directionColor = isCE || isFuture ? "text-profit" : "text-loss";
  const directionArrow = isCE || isFuture ? "\u25B2" : "\u25BC";

  const symbolLabel = isFuture
    ? `${signal.symbol} FUT`
    : `${signal.symbol} ${signal.strike_price > 0 ? signal.strike_price : ""} ${isCE ? "CE" : "PE"}`;

  return (
    <div className="border-b border-border/50 last:border-b-0">
      {/* Main row */}
      <div className="flex items-center gap-2 px-3 py-1.5 hover:bg-bg-tertiary/40 transition-colors">
        {/* Direction + Symbol */}
        <span className={`text-xs font-bold ${directionColor} w-3 shrink-0`}>
          {directionArrow}
        </span>
        <button
          onClick={() => setExpanded(!expanded)}
          className="flex-1 min-w-0 text-left"
        >
          <span className="text-xs font-medium text-text-primary">{symbolLabel}</span>
          <span className="ml-1.5 text-[10px] text-text-muted font-mono">{signal.expiry_date}</span>
        </button>

        {/* Price levels */}
        <div className="flex items-center gap-2 text-[10px] font-mono shrink-0">
          {isUnresolvedOption ? (
            <span className="text-warning">{formatINR(signal.entry_price)}</span>
          ) : (
            <>
              <span className="text-text-secondary">{formatINR(signal.entry_price)}</span>
              {signal.stop_loss > 0 && (
                <span className="text-loss">{formatINR(signal.stop_loss)}</span>
              )}
              {signal.target_price && signal.target_price > 0 && (
                <span className="text-profit">{formatINR(signal.target_price)}</span>
              )}
            </>
          )}
        </div>

        {/* R:R */}
        {!isUnresolvedOption && (
          <span className="text-[10px] font-mono text-text-muted w-8 text-right shrink-0">
            1:{rrRatio}
          </span>
        )}

        {/* Confidence */}
        {signal.confidence != null && (
          <span className={`text-[10px] font-mono font-medium w-8 text-right shrink-0 ${
            signal.confidence >= 70 ? "text-profit" : signal.confidence >= 50 ? "text-accent" : "text-loss"
          }`}>
            {signal.confidence}%
          </span>
        )}

        {/* Strategy badge */}
        <span className="text-[9px] font-mono px-1 py-px rounded bg-accent/10 text-accent shrink-0">
          {STRATEGY_LABELS[signal.strategy_name] || signal.strategy_name}
        </span>

        {/* Action buttons */}
        <div className="flex items-center gap-1 shrink-0 ml-1">
          <button
            onClick={(e) => { e.stopPropagation(); onExecute(signal.id); }}
            className="text-[10px] font-mono font-medium px-1.5 py-0.5 rounded bg-profit/15 text-profit hover:bg-profit/25 transition-colors"
          >
            EXEC
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); onDismiss(signal.id); }}
            className="text-[10px] font-mono px-1 py-0.5 rounded text-text-muted hover:text-loss hover:bg-loss/10 transition-colors"
          >
            ✕
          </button>
        </div>
      </div>

      {/* Expanded reason */}
      {expanded && signal.reason && (
        <div className="px-3 pb-2 pl-8 animate-fade-in">
          <p className="text-[10px] text-text-secondary leading-relaxed font-mono">
            {signal.reason}
          </p>
        </div>
      )}
    </div>
  );
}

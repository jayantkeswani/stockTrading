"use client";

import { useState } from "react";
import { useStore } from "@/store";
import { formatINR } from "@/lib/formatters";
import { STRATEGY_LABELS } from "@/lib/constants";
import { api } from "@/lib/api";

export function ScannerPanel() {
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
      <div className="px-4 py-3 border-b border-border flex items-center justify-between">
        <h2 className="text-sm font-semibold text-text-primary flex items-center gap-2">
          Scanner
          {pendingSignals.length > 0 && (
            <span className="text-xs px-1.5 py-0.5 rounded-full bg-accent/20 text-accent">
              {pendingSignals.length}
            </span>
          )}
        </h2>
        <div className="flex items-center gap-1.5">
          <div className="w-1.5 h-1.5 rounded-full bg-profit animate-pulse" />
          <span className="text-xs text-text-muted">Active</span>
        </div>
      </div>

      {pendingSignals.length === 0 ? (
        <div className="p-10 text-center">
          <div className="relative inline-flex items-center justify-center mb-3">
            <div className="w-10 h-10 rounded-full border border-border flex items-center justify-center">
              <svg className="w-5 h-5 text-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
              </svg>
            </div>
            <div className="absolute inset-0 rounded-full border border-accent/30 animate-ping" style={{ animationDuration: "3s" }} />
          </div>
          <p className="text-sm text-text-muted">Scanner active — waiting for signals</p>
        </div>
      ) : (
        <div className="p-3 grid gap-3 max-h-[400px] overflow-y-auto">
          {pendingSignals.map((signal) => (
            <SignalCard
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

function SignalCard({
  signal,
  onExecute,
  onDismiss,
}: {
  signal: {
    id: string;
    signal_type: "BUY_CE" | "BUY_PE";
    symbol: string;
    strike_price: number;
    expiry_date: string;
    entry_price: number;
    stop_loss: number;
    target_price: number | null;
    confidence: number | null;
    strategy_name: string;
    reason: string;
  };
  onExecute: (id: string) => void;
  onDismiss: (id: string) => void;
}) {
  const [reasonExpanded, setReasonExpanded] = useState(false);
  const isCE = signal.signal_type === "BUY_CE";
  const risk = signal.entry_price - signal.stop_loss;
  const reward = signal.target_price ? signal.target_price - signal.entry_price : 0;
  const totalRange = risk + reward;
  const riskPercent = totalRange > 0 ? (risk / totalRange) * 100 : 50;

  const confidenceColor =
    (signal.confidence ?? 0) >= 70
      ? "bg-profit/20 text-profit"
      : (signal.confidence ?? 0) >= 50
        ? "bg-warning/20 text-warning"
        : "bg-loss/20 text-loss";

  return (
    <div className="rounded-lg border border-border bg-bg-tertiary/50 p-3">
      {/* Top row: direction + symbol + confidence */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className={`text-lg font-bold ${isCE ? "text-profit" : "text-loss"}`}>
            {isCE ? "\u25B2" : "\u25BC"}
          </span>
          <div>
            <span className="text-sm font-semibold text-text-primary">
              {signal.symbol} {signal.strike_price} {isCE ? "CE" : "PE"}
            </span>
            <span className="ml-2 text-xs text-text-muted">{signal.expiry_date}</span>
          </div>
        </div>
        {signal.confidence != null && (
          <span className={`text-xs font-semibold px-2 py-0.5 rounded-full ${confidenceColor}`}>
            {signal.confidence}%
          </span>
        )}
      </div>

      {/* Price levels */}
      <div className="flex items-center gap-3 text-xs font-mono mb-2">
        <span className="text-text-secondary">
          Entry <span className="text-text-primary">{formatINR(signal.entry_price)}</span>
        </span>
        <span className="text-loss">
          SL {formatINR(signal.stop_loss)}
        </span>
        {signal.target_price && (
          <span className="text-profit">
            Tgt {formatINR(signal.target_price)}
          </span>
        )}
      </div>

      {/* Risk:Reward visual bar */}
      {totalRange > 0 && (
        <div className="flex h-1.5 rounded-full overflow-hidden mb-2">
          <div
            className="bg-loss/60 rounded-l-full"
            style={{ width: `${riskPercent}%` }}
          />
          <div
            className="bg-profit/60 rounded-r-full"
            style={{ width: `${100 - riskPercent}%` }}
          />
        </div>
      )}

      {/* Strategy badge */}
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs px-1.5 py-0.5 rounded bg-accent/20 text-accent">
          {STRATEGY_LABELS[signal.strategy_name] || signal.strategy_name}
        </span>
        {totalRange > 0 && (
          <span className="text-xs text-text-muted font-mono">
            R:R 1:{reward > 0 ? (reward / risk).toFixed(1) : "?"}
          </span>
        )}
      </div>

      {/* Reason text (collapsible) */}
      {signal.reason && (
        <button
          onClick={() => setReasonExpanded(!reasonExpanded)}
          className="text-xs text-text-secondary hover:text-text-primary transition-colors text-left w-full mb-3"
        >
          <span className={reasonExpanded ? "" : "line-clamp-2"}>
            {signal.reason}
          </span>
          {!reasonExpanded && signal.reason.length > 100 && (
            <span className="text-accent ml-1">more</span>
          )}
        </button>
      )}

      {/* Action buttons */}
      <div className="flex gap-2">
        <button
          onClick={() => onExecute(signal.id)}
          className="flex-1 text-sm font-semibold py-1.5 rounded bg-profit/20 text-profit hover:bg-profit/30 transition-colors"
        >
          Execute
        </button>
        <button
          onClick={() => onDismiss(signal.id)}
          className="px-3 py-1.5 text-sm rounded bg-bg-tertiary text-text-muted hover:text-text-secondary hover:bg-border transition-colors"
        >
          Dismiss
        </button>
      </div>
    </div>
  );
}

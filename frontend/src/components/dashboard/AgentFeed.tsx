"use client";

import { useState, useMemo } from "react";
import { useStore } from "@/store";
import { STRATEGY_LABELS } from "@/lib/constants";
import { formatINR } from "@/lib/formatters";

const ACTION_COLORS: Record<string, string> = {
  SL_HIT: "text-loss",
  SL_TRIGGERED: "text-loss",
  TARGET_HIT: "text-profit",
  PROFIT_BOOKED: "text-profit",
  ENTRY: "text-accent",
  AUTO_EXECUTED: "text-accent",
  MANUAL_EXECUTED: "text-accent",
  EXIT: "text-warning",
  TIME_EXIT: "text-warning",
  TRAIL_SL: "text-accent",
  SIGNAL_GENERATED: "text-text-secondary",
  PROFIT_BOOK_REQUEST: "text-warning",
  CONFIRMATION_REQUEST: "text-warning",
  DRAWDOWN_HALT: "text-loss",
};

export function AgentFeed() {
  const { agentLogs } = useStore();
  const [filterStrategy, setFilterStrategy] = useState<string>("ALL");
  const [filterAction, setFilterAction] = useState<string>("ALL");

  const strategyNames = useMemo(() => {
    const names = new Set<string>();
    for (const log of agentLogs) {
      const s = (log.details?.strategy_name as string) || "";
      if (s) names.add(s);
    }
    return Array.from(names);
  }, [agentLogs]);

  const actionTypes = useMemo(() => {
    const types = new Set<string>();
    for (const log of agentLogs) {
      if (log.action_type) types.add(log.action_type);
    }
    return Array.from(types);
  }, [agentLogs]);

  const filtered = useMemo(() => {
    let list = agentLogs;
    if (filterStrategy !== "ALL") {
      list = list.filter((log) => (log.details?.strategy_name as string) === filterStrategy);
    }
    if (filterAction !== "ALL") {
      list = list.filter((log) => log.action_type === filterAction);
    }
    return list;
  }, [agentLogs, filterStrategy, filterAction]);

  const formatLogTime = (ts: string) => {
    try {
      return new Date(ts).toLocaleTimeString("en-IN", {
        timeZone: "Asia/Kolkata",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      });
    } catch {
      return "--:--";
    }
  };

  return (
    <div className="rounded border border-border bg-bg-secondary">
      <div className="px-3 py-1.5 border-b border-border">
        <div className="flex items-center justify-between mb-1.5">
          <h2 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
            Agent Feed
          </h2>
        </div>

        {/* Filter bar */}
        <div className="flex gap-1.5">
          <select
            value={filterStrategy}
            onChange={(e) => setFilterStrategy(e.target.value)}
            className="text-xs font-mono bg-bg-tertiary border border-border rounded px-1.5 py-0.5 text-text-secondary focus:outline-none focus:border-accent/50"
          >
            <option value="ALL">All</option>
            {strategyNames.map((s) => (
              <option key={s} value={s}>
                {STRATEGY_LABELS[s] || s}
              </option>
            ))}
          </select>
          <select
            value={filterAction}
            onChange={(e) => setFilterAction(e.target.value)}
            className="text-xs font-mono bg-bg-tertiary border border-border rounded px-1.5 py-0.5 text-text-secondary focus:outline-none focus:border-accent/50"
          >
            <option value="ALL">All</option>
            {actionTypes.map((a) => (
              <option key={a} value={a}>
                {a.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="max-h-[300px] overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="px-3 py-4 text-center text-text-muted text-xs font-mono">
            no activity
          </div>
        ) : (
          <div className="divide-y divide-border/30">
            {filtered.map((log) => {
              const pnl = log.details?.pnl as number | undefined;
              const symbol = log.details?.symbol as string | undefined;
              const strategyKey = (log.details?.strategy_name as string) || "";
              const strategyLabel = STRATEGY_LABELS[strategyKey] || strategyKey;

              return (
                <div
                  key={log.id}
                  className="flex items-center gap-1.5 px-3 py-1 hover:bg-bg-tertiary/30 transition-colors"
                >
                  <span className="text-text-muted text-[10px] font-mono w-8 shrink-0">
                    {formatLogTime(log.created_at)}
                  </span>
                  <span
                    className={`text-xs font-mono font-medium shrink-0 ${ACTION_COLORS[log.action_type] || "text-text-secondary"}`}
                  >
                    {log.action_type.replace(/_/g, " ")}
                  </span>
                  {symbol && (
                    <span className="text-xs font-mono text-text-primary">{symbol}</span>
                  )}
                  {strategyLabel && (
                    <span className="text-[10px] font-mono px-1 py-px rounded bg-accent/10 text-accent shrink-0">
                      {strategyLabel}
                    </span>
                  )}
                  {pnl != null && (
                    <span
                      className={`text-xs font-mono ml-auto shrink-0 ${Number(pnl) >= 0 ? "text-profit" : "text-loss"}`}
                    >
                      {Number(pnl) >= 0 ? "+" : ""}
                      {formatINR(pnl)}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

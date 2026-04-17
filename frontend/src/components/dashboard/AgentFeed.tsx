"use client";

import { useState, useMemo } from "react";
import { useStore } from "@/store";
import { STRATEGY_LABELS } from "@/lib/constants";
import { formatINR } from "@/lib/formatters";

const ACTION_COLORS: Record<string, string> = {
  SL_HIT: "text-loss",
  TARGET_HIT: "text-profit",
  ENTRY: "text-accent",
  EXIT: "text-warning",
  TRAIL_SL: "text-accent",
  SIGNAL_GENERATED: "text-text-secondary",
  CONFIRMATION_REQUEST: "text-warning",
};

const ACTION_TYPE_OPTIONS = [
  "ALL",
  "SL_HIT",
  "TARGET_HIT",
  "ENTRY",
  "EXIT",
  "TRAIL_SL",
  "SIGNAL_GENERATED",
] as const;

export function AgentFeed() {
  const { agentLogs } = useStore();
  const [expandedStrategies, setExpandedStrategies] = useState<Set<string>>(new Set());
  const [filterStrategy, setFilterStrategy] = useState<string>("ALL");
  const [filterAction, setFilterAction] = useState<string>("ALL");

  const grouped = useMemo(() => {
    let filtered = agentLogs;

    if (filterAction !== "ALL") {
      filtered = filtered.filter((log) => log.action_type === filterAction);
    }

    const groups: Record<string, Record<string, typeof agentLogs>> = {};

    for (const log of filtered) {
      const strategy = (log.details?.strategy_name as string) || "unknown";
      const symbol = (log.details?.symbol as string) || "general";

      if (filterStrategy !== "ALL" && strategy !== filterStrategy) continue;

      if (!groups[strategy]) groups[strategy] = {};
      if (!groups[strategy][symbol]) groups[strategy][symbol] = [];
      groups[strategy][symbol].push(log);
    }

    return groups;
  }, [agentLogs, filterStrategy, filterAction]);

  const strategyNames = useMemo(() => {
    const names = new Set<string>();
    for (const log of agentLogs) {
      const s = (log.details?.strategy_name as string) || "unknown";
      names.add(s);
    }
    return Array.from(names);
  }, [agentLogs]);

  const toggleStrategy = (strategy: string) => {
    setExpandedStrategies((prev) => {
      const next = new Set(prev);
      if (next.has(strategy)) {
        next.delete(strategy);
      } else {
        next.add(strategy);
      }
      return next;
    });
  };

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
          <h2 className="text-[11px] font-mono font-medium text-text-secondary uppercase tracking-wider">
            Agent Feed
          </h2>
        </div>

        {/* Filter bar */}
        <div className="flex gap-1.5">
          <select
            value={filterStrategy}
            onChange={(e) => setFilterStrategy(e.target.value)}
            className="text-[10px] font-mono bg-bg-tertiary border border-border rounded px-1.5 py-0.5 text-text-secondary focus:outline-none focus:border-accent/50"
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
            className="text-[10px] font-mono bg-bg-tertiary border border-border rounded px-1.5 py-0.5 text-text-secondary focus:outline-none focus:border-accent/50"
          >
            {ACTION_TYPE_OPTIONS.map((a) => (
              <option key={a} value={a}>
                {a === "ALL" ? "All" : a.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="max-h-[300px] overflow-y-auto">
        {Object.keys(grouped).length === 0 ? (
          <div className="px-3 py-4 text-center text-text-muted text-[10px] font-mono">
            no activity
          </div>
        ) : (
          Object.entries(grouped).map(([strategy, symbols]) => {
            const isExpanded = expandedStrategies.has(strategy);
            const totalLogs = Object.values(symbols).reduce(
              (sum, logs) => sum + logs.length,
              0
            );

            return (
              <div key={strategy} className="border-b border-border/30 last:border-b-0">
                <button
                  onClick={() => toggleStrategy(strategy)}
                  className="w-full flex items-center justify-between px-3 py-1 hover:bg-bg-tertiary/40 transition-colors text-left"
                >
                  <div className="flex items-center gap-1.5">
                    <span className="text-[9px] text-text-muted font-mono">
                      {isExpanded ? "\u25BE" : "\u25B8"}
                    </span>
                    <span className="text-[11px] font-mono font-medium text-text-primary">
                      {STRATEGY_LABELS[strategy] || strategy}
                    </span>
                  </div>
                  <span className="text-[9px] text-text-muted font-mono">{totalLogs}</span>
                </button>

                {isExpanded && (
                  <div className="pb-1 animate-fade-in">
                    {Object.entries(symbols).map(([symbol, logs]) => (
                      <div key={symbol} className="ml-3">
                        {symbol !== "general" && (
                          <div className="px-3 py-0.5 text-[10px] font-mono font-medium text-text-secondary">
                            {symbol}
                          </div>
                        )}
                        {logs.map((log) => {
                          const pnl = log.details?.pnl as number | undefined;
                          return (
                            <div
                              key={log.id}
                              className="flex items-center gap-1.5 px-3 py-0.5 text-[10px] font-mono"
                            >
                              <span className="text-text-muted w-8 shrink-0">
                                {formatLogTime(log.created_at)}
                              </span>
                              <span
                                className={`font-medium ${ACTION_COLORS[log.action_type] || "text-text-secondary"}`}
                              >
                                {log.action_type.replace(/_/g, " ")}
                              </span>
                              {pnl != null && (
                                <span
                                  className={`ml-auto ${pnl >= 0 ? "text-profit" : "text-loss"}`}
                                >
                                  {pnl >= 0 ? "+" : ""}
                                  {formatINR(pnl)}
                                </span>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}

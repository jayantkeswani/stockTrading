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

  // Group logs by strategy, then by symbol/trade
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
    <div className="rounded-lg border border-border bg-bg-secondary">
      <div className="px-4 py-3 border-b border-border">
        <h2 className="text-sm font-semibold text-text-primary mb-2">Agent Feed</h2>

        {/* Filter bar */}
        <div className="flex gap-2">
          <select
            value={filterStrategy}
            onChange={(e) => setFilterStrategy(e.target.value)}
            className="text-xs bg-bg-tertiary border border-border rounded px-2 py-1 text-text-secondary focus:outline-none focus:border-accent"
          >
            <option value="ALL">All Strategies</option>
            {strategyNames.map((s) => (
              <option key={s} value={s}>
                {STRATEGY_LABELS[s] || s}
              </option>
            ))}
          </select>
          <select
            value={filterAction}
            onChange={(e) => setFilterAction(e.target.value)}
            className="text-xs bg-bg-tertiary border border-border rounded px-2 py-1 text-text-secondary focus:outline-none focus:border-accent"
          >
            {ACTION_TYPE_OPTIONS.map((a) => (
              <option key={a} value={a}>
                {a === "ALL" ? "All Actions" : a.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="max-h-[400px] overflow-y-auto">
        {Object.keys(grouped).length === 0 ? (
          <div className="p-6 text-center text-text-muted text-sm">
            No agent activity
          </div>
        ) : (
          Object.entries(grouped).map(([strategy, symbols]) => {
            const isExpanded = expandedStrategies.has(strategy);
            const totalLogs = Object.values(symbols).reduce(
              (sum, logs) => sum + logs.length,
              0
            );

            return (
              <div key={strategy} className="border-b border-border/50 last:border-b-0">
                {/* Strategy header */}
                <button
                  onClick={() => toggleStrategy(strategy)}
                  className="w-full flex items-center justify-between px-4 py-2.5 hover:bg-bg-tertiary/50 transition-colors text-left"
                >
                  <div className="flex items-center gap-2">
                    <span className="text-xs text-text-muted">
                      {isExpanded ? "\u25BE" : "\u25B8"}
                    </span>
                    <span className="text-sm font-medium text-text-primary">
                      {STRATEGY_LABELS[strategy] || strategy}
                    </span>
                  </div>
                  <span className="text-xs text-text-muted">{totalLogs}</span>
                </button>

                {/* Expanded content */}
                {isExpanded && (
                  <div className="pb-2">
                    {Object.entries(symbols).map(([symbol, logs]) => (
                      <div key={symbol} className="ml-4">
                        {symbol !== "general" && (
                          <div className="px-4 py-1 text-xs font-medium text-text-secondary">
                            {symbol}
                          </div>
                        )}
                        {logs.map((log) => {
                          const pnl = log.details?.pnl as number | undefined;
                          return (
                            <div
                              key={log.id}
                              className="flex items-center gap-2 px-4 py-1 text-xs"
                            >
                              <span className="text-text-muted font-mono w-10 shrink-0">
                                {formatLogTime(log.created_at)}
                              </span>
                              <span
                                className={`font-medium ${ACTION_COLORS[log.action_type] || "text-text-secondary"}`}
                              >
                                {log.action_type.replace(/_/g, " ")}
                              </span>
                              {pnl != null && (
                                <span
                                  className={`font-mono ml-auto ${pnl >= 0 ? "text-profit" : "text-loss"}`}
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

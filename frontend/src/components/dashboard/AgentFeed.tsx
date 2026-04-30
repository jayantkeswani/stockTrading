"use client";

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
        <h2 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          Agent Feed
        </h2>
      </div>

      <div className="max-h-[300px] overflow-y-auto">
        {agentLogs.length === 0 ? (
          <div className="px-3 py-4 text-center text-text-muted text-xs font-mono">
            no activity
          </div>
        ) : (
          <div className="divide-y divide-border/30">
            {agentLogs.map((log) => {
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

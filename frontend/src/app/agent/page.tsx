"use client";

import { useEffect, useState } from "react";
import { useStore } from "@/store";
import { api } from "@/lib/api";
import { formatTime } from "@/lib/formatters";
import type { AgentLog } from "@/lib/types";

export default function AgentPage() {
  const { agentStatus, setAgentStatus } = useStore();
  const [logs, setLogs] = useState<AgentLog[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function load() {
      try {
        const [status, agentLogs] = await Promise.all([
          api.getAgentStatus(),
          api.getAgentLogs(),
        ]);
        setAgentStatus(status as never);
        setLogs(agentLogs as AgentLog[]);
      } catch {
        // API not running yet
      }
      setLoading(false);
    }
    load();
  }, [setAgentStatus]);

  const handleToggle = async () => {
    try {
      if (agentStatus?.running) {
        await api.stopAgent();
        setAgentStatus({ ...agentStatus, running: false } as never);
      } else {
        await api.startAgent();
        setAgentStatus({ ...(agentStatus || {}), running: true } as never);
      }
    } catch (err) {
      console.error("Failed to toggle agent:", err);
    }
  };

  const handleToggleYolo = async () => {
    try {
      const newState = !agentStatus?.yolo_mode;
      await api.toggleYolo(newState);
      setAgentStatus({
        ...(agentStatus || {}),
        yolo_mode: newState,
        autonomy_level: newState ? "yolo" : "semi",
      } as never);
    } catch (err) {
      console.error("Failed to toggle YOLO mode:", err);
    }
  };

  const handleConfirm = async (logId: string, approved: boolean) => {
    try {
      await api.confirmAction(logId, approved);
      setLogs((prev) =>
        prev.map((l) =>
          l.id === logId
            ? { ...l, confirmation_status: approved ? "APPROVED" : "REJECTED" }
            : l
        )
      );
    } catch (err) {
      console.error("Failed to confirm:", err);
    }
  };

  const actionTypeColors: Record<string, string> = {
    SL_TRIGGERED: "text-loss",
    PROFIT_BOOK_REQUEST: "text-warning",
    PROFIT_BOOKED: "text-profit",
    TIME_EXIT: "text-text-secondary",
    DRAWDOWN_HALT: "text-loss",
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h1 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          AI Agent
        </h1>
        <div className="flex items-center gap-2">
          {/* YOLO Mode Toggle */}
          <button
            onClick={handleToggleYolo}
            className={`flex items-center gap-1.5 px-2 py-1 rounded text-[10px] font-mono font-medium transition-colors border ${
              agentStatus?.yolo_mode
                ? "border-warning/40 bg-warning/10 text-warning"
                : "border-border bg-bg-secondary text-text-muted hover:text-text-secondary"
            }`}
          >
            <div
              className={`w-6 h-3 rounded-full relative transition-colors ${
                agentStatus?.yolo_mode ? "bg-warning/30" : "bg-bg-tertiary"
              }`}
            >
              <div
                className={`absolute top-0.5 w-2 h-2 rounded-full transition-all ${
                  agentStatus?.yolo_mode
                    ? "left-3.5 bg-warning"
                    : "left-0.5 bg-text-muted"
                }`}
              />
            </div>
            YOLO
          </button>

          {/* Autonomy level badge */}
          <span className={`text-[9px] font-mono px-1.5 py-0.5 rounded uppercase ${
            agentStatus?.autonomy_level === "yolo"
              ? "bg-warning/15 text-warning"
              : agentStatus?.autonomy_level === "semi"
                ? "bg-accent/15 text-accent"
                : "bg-bg-tertiary text-text-muted"
          }`}>
            {agentStatus?.autonomy_level ?? "semi"}
          </span>

          {/* Start/Stop button */}
          <button
            onClick={handleToggle}
            className={`px-2.5 py-1 rounded text-[10px] font-mono font-medium transition-colors ${
              agentStatus?.running
                ? "bg-loss/15 text-loss hover:bg-loss/25"
                : "bg-profit/15 text-profit hover:bg-profit/25"
            }`}
          >
            {agentStatus?.running ? "STOP" : "START"}
          </button>
        </div>
      </div>

      {/* Status Cards */}
      <div className="grid grid-cols-5 gap-2">
        <div className="rounded border border-border bg-bg-secondary px-3 py-2">
          <div className="text-[9px] text-text-muted font-mono uppercase mb-1">Status</div>
          <div className="flex items-center gap-1.5">
            <div
              className={`w-2 h-2 rounded-full ${
                agentStatus?.running ? "bg-profit animate-pulse" : "bg-text-muted"
              }`}
            />
            <span className="text-[11px] font-mono font-medium">
              {agentStatus?.running ? "RUNNING" : "STOPPED"}
            </span>
          </div>
        </div>
        <div className={`rounded border px-3 py-2 ${
          agentStatus?.yolo_mode ? "border-warning/30 bg-warning/5" : "border-border bg-bg-secondary"
        }`}>
          <div className="text-[9px] text-text-muted font-mono uppercase mb-1">Mode</div>
          <div className={`text-sm font-mono font-bold ${
            agentStatus?.yolo_mode ? "text-warning" : "text-accent"
          }`}>
            {agentStatus?.yolo_mode ? "YOLO" : "SEMI"}
          </div>
        </div>
        <div className="rounded border border-border bg-bg-secondary px-3 py-2">
          <div className="text-[9px] text-text-muted font-mono uppercase mb-1">Pending</div>
          <div className="text-sm font-mono font-bold">
            {agentStatus?.pending_confirmations ?? 0}
          </div>
        </div>
        <div className="rounded border border-border bg-bg-secondary px-3 py-2">
          <div className="text-[9px] text-text-muted font-mono uppercase mb-1">Monitored</div>
          <div className="text-sm font-mono font-bold">
            {agentStatus?.positions_monitored ?? 0}
          </div>
        </div>
        <div className="rounded border border-border bg-bg-secondary px-3 py-2">
          <div className="text-[9px] text-text-muted font-mono uppercase mb-1">Uptime</div>
          <div className="text-sm font-mono font-bold">
            {agentStatus?.uptime_seconds
              ? `${Math.floor(agentStatus.uptime_seconds / 60)}m`
              : "\u2014"}
          </div>
        </div>
      </div>

      {/* Agent Logs */}
      <div className="rounded border border-border bg-bg-secondary">
        <div className="px-3 py-1.5 border-b border-border">
          <h2 className="text-[11px] font-mono font-medium text-text-secondary uppercase tracking-wider">
            Activity Log
          </h2>
        </div>
        {loading ? (
          <div className="px-3 py-6 text-center text-text-muted text-[10px] font-mono">loading...</div>
        ) : logs.length === 0 ? (
          <div className="px-3 py-6 text-center text-text-muted text-[10px] font-mono">no activity yet</div>
        ) : (
          <div className="divide-y divide-border/30 max-h-[400px] overflow-y-auto">
            {logs.map((log) => (
              <div key={log.id} className="px-3 py-1.5 hover:bg-bg-tertiary/30">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-[10px] font-mono font-bold ${
                        actionTypeColors[log.action_type] || "text-text-secondary"
                      }`}
                    >
                      {log.action_type}
                    </span>
                    {log.details?.symbol != null && (
                      <span className="text-[11px] font-mono">{String(log.details.symbol)}</span>
                    )}
                  </div>
                  <span className="text-[9px] text-text-muted font-mono">
                    {formatTime(log.created_at)}
                  </span>
                </div>
                {log.details?.message != null && (
                  <p className="text-[10px] font-mono text-text-muted mt-0.5">
                    {String(log.details.message)}
                  </p>
                )}
                {log.requires_confirmation &&
                  log.confirmation_status === "PENDING" && (
                    <div className="mt-1 flex gap-1.5">
                      <button
                        onClick={() => handleConfirm(log.id, true)}
                        className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-profit/15 text-profit hover:bg-profit/25"
                      >
                        APPROVE
                      </button>
                      <button
                        onClick={() => handleConfirm(log.id, false)}
                        className="text-[9px] font-mono px-1.5 py-0.5 rounded bg-loss/15 text-loss hover:bg-loss/25"
                      >
                        REJECT
                      </button>
                    </div>
                  )}
                {log.confirmation_status && log.confirmation_status !== "PENDING" && (
                  <span
                    className={`text-[9px] font-mono mt-0.5 inline-block ${
                      log.confirmation_status === "APPROVED"
                        ? "text-profit"
                        : "text-loss"
                    }`}
                  >
                    {log.confirmation_status}
                  </span>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

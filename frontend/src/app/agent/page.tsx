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
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-semibold">AI Agent</h1>
        <div className="flex items-center gap-3">
          {/* YOLO Mode Toggle */}
          <button
            onClick={handleToggleYolo}
            className={`flex items-center gap-2 px-3 py-2 rounded-lg text-sm font-medium transition-colors border ${
              agentStatus?.yolo_mode
                ? "border-warning bg-warning/10 text-warning"
                : "border-border bg-bg-secondary text-text-secondary hover:text-text-primary"
            }`}
          >
            <div
              className={`w-8 h-4 rounded-full relative transition-colors ${
                agentStatus?.yolo_mode ? "bg-warning/40" : "bg-bg-tertiary"
              }`}
            >
              <div
                className={`absolute top-0.5 w-3 h-3 rounded-full transition-all ${
                  agentStatus?.yolo_mode
                    ? "left-4.5 bg-warning"
                    : "left-0.5 bg-text-muted"
                }`}
              />
            </div>
            YOLO
            {agentStatus?.yolo_mode && (
              <span className="text-xs opacity-75">
                (auto-execute + auto-profit)
              </span>
            )}
          </button>

          {/* Autonomy level badge */}
          <span className={`text-xs px-2 py-1 rounded font-mono uppercase ${
            agentStatus?.autonomy_level === "yolo"
              ? "bg-warning/20 text-warning"
              : agentStatus?.autonomy_level === "semi"
                ? "bg-accent/20 text-accent"
                : "bg-bg-tertiary text-text-muted"
          }`}>
            {agentStatus?.autonomy_level ?? "semi"}
          </span>

          {/* Start/Stop button */}
          <button
            onClick={handleToggle}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
              agentStatus?.running
                ? "bg-loss/20 text-loss hover:bg-loss/30"
                : "bg-profit/20 text-profit hover:bg-profit/30"
            }`}
          >
            {agentStatus?.running ? "Stop Agent" : "Start Agent"}
          </button>
        </div>
      </div>

      {/* Status Cards */}
      <div className="grid grid-cols-5 gap-4">
        <div className="rounded-lg border border-border bg-bg-secondary p-4">
          <div className="text-xs text-text-muted mb-1">Status</div>
          <div className="flex items-center gap-2">
            <div
              className={`w-3 h-3 rounded-full ${
                agentStatus?.running ? "bg-profit animate-pulse" : "bg-text-muted"
              }`}
            />
            <span className="font-medium">
              {agentStatus?.running ? "Running" : "Stopped"}
            </span>
          </div>
        </div>
        <div className={`rounded-lg border p-4 ${
          agentStatus?.yolo_mode ? "border-warning/50 bg-warning/5" : "border-border bg-bg-secondary"
        }`}>
          <div className="text-xs text-text-muted mb-1">Mode</div>
          <div className={`text-lg font-bold ${
            agentStatus?.yolo_mode ? "text-warning" : "text-accent"
          }`}>
            {agentStatus?.yolo_mode ? "YOLO" : "SEMI"}
          </div>
          <div className="text-xs text-text-muted mt-0.5">
            {agentStatus?.yolo_mode
              ? "Auto-execute all"
              : "SL auto, profit confirm"}
          </div>
        </div>
        <div className="rounded-lg border border-border bg-bg-secondary p-4">
          <div className="text-xs text-text-muted mb-1">Pending Confirmations</div>
          <div className="text-xl font-bold font-mono">
            {agentStatus?.pending_confirmations ?? 0}
          </div>
        </div>
        <div className="rounded-lg border border-border bg-bg-secondary p-4">
          <div className="text-xs text-text-muted mb-1">Positions Monitored</div>
          <div className="text-xl font-bold font-mono">
            {agentStatus?.positions_monitored ?? 0}
          </div>
        </div>
        <div className="rounded-lg border border-border bg-bg-secondary p-4">
          <div className="text-xs text-text-muted mb-1">Uptime</div>
          <div className="text-xl font-bold font-mono">
            {agentStatus?.uptime_seconds
              ? `${Math.floor(agentStatus.uptime_seconds / 60)}m`
              : "—"}
          </div>
        </div>
      </div>

      {/* Agent Logs */}
      <div className="rounded-lg border border-border bg-bg-secondary">
        <div className="px-4 py-3 border-b border-border">
          <h2 className="text-sm font-semibold">Agent Activity Log</h2>
        </div>
        {loading ? (
          <div className="p-8 text-center text-text-muted">Loading...</div>
        ) : logs.length === 0 ? (
          <div className="p-8 text-center text-text-muted">No agent activity yet</div>
        ) : (
          <div className="divide-y divide-border/50 max-h-[500px] overflow-y-auto">
            {logs.map((log) => (
              <div key={log.id} className="px-4 py-3 hover:bg-bg-tertiary/30">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <span
                      className={`text-xs font-mono font-bold ${
                        actionTypeColors[log.action_type] || "text-text-secondary"
                      }`}
                    >
                      {log.action_type}
                    </span>
                    {log.details?.symbol != null && (
                      <span className="text-sm">{String(log.details.symbol)}</span>
                    )}
                  </div>
                  <span className="text-xs text-text-muted font-mono">
                    {formatTime(log.created_at)}
                  </span>
                </div>
                {log.details?.message != null && (
                  <p className="text-xs text-text-secondary mt-1">
                    {String(log.details.message)}
                  </p>
                )}
                {log.requires_confirmation &&
                  log.confirmation_status === "PENDING" && (
                    <div className="mt-2 flex gap-2">
                      <button
                        onClick={() => handleConfirm(log.id, true)}
                        className="text-xs px-3 py-1 rounded bg-profit/20 text-profit hover:bg-profit/30"
                      >
                        Approve
                      </button>
                      <button
                        onClick={() => handleConfirm(log.id, false)}
                        className="text-xs px-3 py-1 rounded bg-loss/20 text-loss hover:bg-loss/30"
                      >
                        Reject
                      </button>
                    </div>
                  )}
                {log.confirmation_status && log.confirmation_status !== "PENDING" && (
                  <span
                    className={`text-xs mt-1 inline-block ${
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

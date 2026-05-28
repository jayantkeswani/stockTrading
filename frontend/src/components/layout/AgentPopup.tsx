"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import { useStore } from "@/store";
import { useShallow } from "zustand/react/shallow";
import { api } from "@/lib/api";
import { formatINR } from "@/lib/formatters";
import type { AgentStatus, RiskDashboard } from "@/lib/types";

export function AgentPopup() {
  const { agentStatus, setAgentStatus, risk, setRisk } = useStore(useShallow((s) => ({
    agentStatus: s.agentStatus,
    setAgentStatus: s.setAgentStatus,
    risk: s.risk,
    setRisk: s.setRisk,
  })));
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, [open]);

  const refreshStatus = useCallback(async () => {
    try {
      const [status, riskData] = await Promise.all([
        api.getAgentStatus() as Promise<AgentStatus>,
        api.getRiskDashboard() as Promise<RiskDashboard>,
      ]);
      setAgentStatus(status as never);
      setRisk(riskData);
    } catch {
      // ignore
    }
  }, [setAgentStatus, setRisk]);

  // Fetch fresh risk + status when popup opens
  useEffect(() => {
    if (open) refreshStatus();
  }, [open, refreshStatus]);

  const handleStartStop = useCallback(async () => {
    setLoading("power");
    try {
      if (agentStatus?.running) {
        await api.stopAgent();
      } else {
        await api.startAgent();
      }
      // Small delay for backend state to settle
      await new Promise((r) => setTimeout(r, 500));
      await refreshStatus();
    } catch (err) {
      console.error("Agent start/stop failed:", err);
    } finally {
      setLoading(null);
    }
  }, [agentStatus?.running, refreshStatus]);

  const handleToggleYolo = useCallback(async (enabled: boolean) => {
    setLoading("yolo");
    try {
      await api.toggleYolo(enabled);
      await new Promise((r) => setTimeout(r, 300));
      await refreshStatus();
    } catch (err) {
      console.error("YOLO toggle failed:", err);
    } finally {
      setLoading(null);
    }
  }, [refreshStatus]);

  const running = agentStatus?.running ?? false;
  const yolo = agentStatus?.yolo_mode ?? false;

  const dotColor = running
    ? yolo
      ? "bg-warning animate-pulse"
      : "bg-profit animate-pulse"
    : "bg-text-muted";

  const label = running
    ? yolo
      ? "YOLO"
      : "AGENT"
    : "AGENT OFF";

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 hover:opacity-80 transition-opacity"
      >
        <div className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
        <span className="text-[10px] font-mono text-text-muted">{label}</span>
      </button>

      {open && (
        <div className="absolute top-6 right-0 w-56 bg-bg-elevated border border-border rounded shadow-lg z-50 animate-fade-in">
          {/* Header */}
          <div className="flex items-center justify-between px-3 py-1.5 border-b border-border">
            <span className="text-[10px] font-mono font-medium text-text-secondary uppercase tracking-wider">
              agent control
            </span>
            <span className={`text-[9px] font-mono ${running ? "text-profit" : "text-text-muted"}`}>
              {running ? "running" : "stopped"}
            </span>
          </div>

          {/* Controls */}
          <div className="px-3 py-2 space-y-2">
            {/* Start / Stop */}
            <button
              onClick={handleStartStop}
              disabled={loading === "power"}
              className={`w-full text-[10px] font-mono py-1.5 rounded transition-colors ${
                running
                  ? "bg-loss/15 text-loss hover:bg-loss/25"
                  : "bg-profit/15 text-profit hover:bg-profit/25"
              } ${loading === "power" ? "opacity-50 cursor-wait" : ""}`}
            >
              {loading === "power"
                ? "..."
                : running
                  ? "STOP AGENT"
                  : "START AGENT"}
            </button>

            {/* Mode selection — only when running */}
            {running && (
              <div className="flex gap-1">
                <button
                  onClick={() => handleToggleYolo(false)}
                  disabled={loading === "yolo" || !yolo}
                  className={`flex-1 text-[10px] font-mono py-1 rounded transition-colors ${
                    !yolo
                      ? "bg-accent/20 text-accent border border-accent/30"
                      : "bg-bg-tertiary text-text-muted hover:bg-bg-tertiary/80"
                  } ${loading === "yolo" ? "opacity-50 cursor-wait" : ""}`}
                >
                  SEMI
                </button>
                <button
                  onClick={() => handleToggleYolo(true)}
                  disabled={loading === "yolo" || yolo}
                  className={`flex-1 text-[10px] font-mono py-1 rounded transition-colors ${
                    yolo
                      ? "bg-warning/20 text-warning border border-warning/30"
                      : "bg-bg-tertiary text-text-muted hover:bg-bg-tertiary/80"
                  } ${loading === "yolo" ? "opacity-50 cursor-wait" : ""}`}
                >
                  YOLO
                </button>
              </div>
            )}

            {/* Status details */}
            {running && agentStatus && (
              <div className="border-t border-border/50 pt-1.5 space-y-0.5">
                <div className="flex justify-between text-[9px] font-mono">
                  <span className="text-text-muted">positions</span>
                  <span className="text-text-secondary">{agentStatus.positions_monitored}</span>
                </div>
                <div className="flex justify-between text-[9px] font-mono">
                  <span className="text-text-muted">pending confirms</span>
                  <span className={agentStatus.pending_confirmations > 0 ? "text-warning" : "text-text-secondary"}>
                    {agentStatus.pending_confirmations}
                  </span>
                </div>
                {agentStatus.uptime_seconds != null && (
                  <div className="flex justify-between text-[9px] font-mono">
                    <span className="text-text-muted">uptime</span>
                    <span className="text-text-secondary">{formatUptime(agentStatus.uptime_seconds)}</span>
                  </div>
                )}
                {risk?.profiles && risk.profiles.length > 0 && risk.profiles.map((p) => (
                  <div key={p.id} className="flex justify-between text-[9px] font-mono">
                    <span className="text-text-muted">{p.name}</span>
                    <span className={p.is_capped ? "text-profit font-bold" : "text-text-secondary"}>
                      {p.is_capped ? "HIT" : `${formatINR(p.current_pnl)} / ${formatINR(p.profit_cap)}`}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  return `${h}h ${m}m`;
}

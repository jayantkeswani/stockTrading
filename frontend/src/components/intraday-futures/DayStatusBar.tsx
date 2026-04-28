"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { S5DailyStats } from "@/lib/types";

const PHASE_COLORS: Record<string, string> = {
  PRE_MARKET: "bg-text-muted/20 text-text-muted",
  ORB_FORMING: "bg-warning/20 text-warning",
  MORNING_ACTIVE: "bg-profit/20 text-profit",
  CAUTION_ZONE: "bg-loss/20 text-loss",
  AFTERNOON: "bg-accent/20 text-accent",
  CLOSING: "bg-text-muted/20 text-text-muted",
  DONE: "bg-text-muted/20 text-text-muted",
};

export function DayStatusBar({
  date,
  onDateChange,
}: {
  date: string | null;
  onDateChange: (date: string | null) => void;
}) {
  const [phase, setPhase] = useState("—");
  const [agentStatus, setAgentStatus] = useState("ACTIVE");
  const [stats, setStats] = useState<S5DailyStats>({ total_trades: 0, active_positions: 0, closed_trades: 0, wins: 0, losses: 0, net_pnl: 0, win_rate: 0 });
  const [loading, setLoading] = useState(false);

  const isHistorical = date != null;

  const fetchData = async () => {
    try {
      const [phaseRes, statusRes, statsRes] = await Promise.all([
        api.getIntradayFuturesPhase(date ?? undefined),
        isHistorical ? Promise.resolve({ status: "—" }) : api.getIntradayFuturesAgentStatus(),
        api.getIntradayFuturesDailyStats(date ?? undefined),
      ]);
      setPhase(phaseRes.phase);
      setAgentStatus(statusRes.status);
      setStats(statsRes);
    } catch {
      /* silent */
    }
  };

  useEffect(() => {
    fetchData();
    if (isHistorical) return;
    const interval = setInterval(fetchData, 5000);
    return () => clearInterval(interval);
  }, [date]);

  const toggleAgent = async () => {
    const action = agentStatus === "ACTIVE" ? "pause" : "resume";
    await api.setIntradayFuturesAgentAction(action);
    setAgentStatus(action === "pause" ? "PAUSED" : "ACTIVE");
  };

  const runScreener = async () => {
    setLoading(true);
    try {
      await api.runIntradayFuturesScreener();
    } finally {
      setLoading(false);
    }
  };

  const runBriefing = async () => {
    setLoading(true);
    try {
      await api.runIntradayFuturesBriefing();
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex items-center gap-3 px-3 py-1.5 bg-bg-secondary border border-border rounded">
      {/* Phase */}
      <span className={`text-[10px] font-mono px-1.5 py-px rounded ${PHASE_COLORS[phase] || "bg-bg-tertiary text-text-muted"}`}>
        {phase.replace("_", " ")}
      </span>

      <div className="w-px h-4 bg-border" />

      {/* Agent status */}
      {!isHistorical && (
        <>
          <span className={`text-[10px] font-mono px-1.5 py-px rounded ${
            agentStatus === "ACTIVE" ? "bg-profit/20 text-profit" :
            agentStatus === "HALTED" ? "bg-loss/20 text-loss" :
            "bg-warning/20 text-warning"
          }`}>
            {agentStatus}
          </span>
          <div className="w-px h-4 bg-border" />
        </>
      )}

      {/* Stats */}
      <span className="text-xs font-mono text-text-secondary">
        Trades: {stats.total_trades}/{5}
      </span>
      <span className="text-xs font-mono text-text-secondary">
        Pos: {stats.active_positions}/{3}
      </span>
      <span className={`text-xs font-mono ${stats.net_pnl >= 0 ? "text-profit" : "text-loss"}`}>
        P&L: {stats.net_pnl.toFixed(0)}
      </span>

      <div className="flex-1" />

      {/* Date picker */}
      <input
        type="date"
        value={date ?? ""}
        onChange={(e) => onDateChange(e.target.value || null)}
        className="text-[10px] font-mono bg-bg-tertiary border border-border rounded px-1.5 py-0.5 text-text-secondary"
      />
      {isHistorical && (
        <button
          onClick={() => onDateChange(null)}
          className="text-[10px] font-mono px-1.5 py-0.5 rounded border border-accent/30 text-accent hover:bg-accent/10"
        >
          Today
        </button>
      )}

      {/* Actions (only for live/today) */}
      {!isHistorical && (
        <>
          <button
            onClick={runBriefing}
            disabled={loading}
            className="text-[10px] font-mono px-2 py-0.5 rounded border border-border hover:bg-bg-tertiary text-text-secondary disabled:opacity-50"
          >
            Briefing
          </button>
          <button
            onClick={runScreener}
            disabled={loading}
            className="text-[10px] font-mono px-2 py-0.5 rounded border border-border hover:bg-bg-tertiary text-text-secondary disabled:opacity-50"
          >
            Screener
          </button>
          <button
            onClick={toggleAgent}
            className={`text-[10px] font-mono px-2 py-0.5 rounded border ${
              agentStatus === "ACTIVE"
                ? "border-loss/30 text-loss hover:bg-loss/10"
                : "border-profit/30 text-profit hover:bg-profit/10"
            }`}
          >
            {agentStatus === "ACTIVE" ? "Pause" : "Resume"}
          </button>
        </>
      )}
    </div>
  );
}

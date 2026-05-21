"use client";

import { useEffect, useMemo, useState } from "react";
import { useStore } from "@/store";
import { api } from "@/lib/api";
import { formatTime, formatDate, startOfDayIST, endOfDayIST } from "@/lib/formatters";
import { STRATEGY_LABELS } from "@/lib/constants";
import type { AgentLog } from "@/lib/types";
import { PeriodFilter, type Period } from "@/components/trades/PeriodFilter";

function defaultPeriod(): Period {
  const now = new Date();
  return { start: startOfDayIST(now), end: endOfDayIST(now), label: "Today" };
}

const ACTION_COLORS: Record<string, string> = {
  SL_TRIGGERED: "text-loss",
  SL_HIT: "text-loss",
  DRAWDOWN_HALT: "text-loss",
  PROFIT_BOOK_REQUEST: "text-warning",
  TIME_EXIT: "text-warning",
  EXPIRY_ROLL: "text-warning",
  PROFIT_BOOKED: "text-profit",
  TARGET_HIT: "text-profit",
  AUTO_EXECUTED: "text-accent",
  MANUAL_EXECUTED: "text-accent",
  SHADOW_EXECUTED: "text-purple-400",
};

export default function AgentPage() {
  const { agentStatus, setAgentStatus } = useStore();
  const [logs, setLogs] = useState<AgentLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [period, setPeriod] = useState<Period>(defaultPeriod);

  // Filters
  const [actionFilter, setActionFilter] = useState<string>("");
  const [strategyFilter, setStrategyFilter] = useState<string>("");
  const [symbolQuery, setSymbolQuery] = useState<string>("");
  const [pendingOnly, setPendingOnly] = useState(false);
  const [shadowFilter, setShadowFilter] = useState<"all" | "real" | "shadow">("all");

  useEffect(() => {
    async function loadStatus() {
      try {
        const status = await api.getAgentStatus();
        setAgentStatus(status as never);
      } catch {
        // API not running yet
      }
    }
    loadStatus();
  }, [setAgentStatus]);

  useEffect(() => {
    setLoading(true);
    let cancelled = false;
    async function loadLogs() {
      try {
        const agentLogs = await api.getAgentLogs({
          since: period.start.toISOString(),
          until: period.end.toISOString(),
        });
        if (!cancelled) setLogs(agentLogs as AgentLog[]);
      } catch {
        if (!cancelled) setLogs([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadLogs();
    return () => { cancelled = true; };
  }, [period]);

  // Reset filters when period changes so stale filter pills don't orphan
  useEffect(() => {
    setActionFilter("");
    setStrategyFilter("");
    setSymbolQuery("");
    setPendingOnly(false);
    setShadowFilter("all");
  }, [period]);

  const actionTypes = useMemo(() => {
    const s = new Set<string>();
    for (const l of logs) if (l.action_type) s.add(l.action_type);
    return Array.from(s).sort();
  }, [logs]);

  const strategies = useMemo(() => {
    const s = new Set<string>();
    for (const l of logs) {
      const name = l.details?.strategy_name as string | undefined;
      if (name) s.add(name);
    }
    return Array.from(s).sort();
  }, [logs]);

  const displayed = useMemo(() => {
    const q = symbolQuery.trim().toUpperCase();
    return logs.filter((l) => {
      if (actionFilter && l.action_type !== actionFilter) return false;
      if (strategyFilter && (l.details?.strategy_name as string | undefined) !== strategyFilter) return false;
      if (q && !String(l.details?.symbol ?? "").toUpperCase().includes(q)) return false;
      if (pendingOnly && l.confirmation_status !== "PENDING") return false;
      const isShadow = l.action_type === "SHADOW_EXECUTED" || l.details?.is_shadow === true;
      if (shadowFilter === "shadow" && !isShadow) return false;
      if (shadowFilter === "real" && isShadow) return false;
      return true;
    });
  }, [logs, actionFilter, strategyFilter, symbolQuery, pendingOnly, shadowFilter]);

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

  const hasActiveFilters = actionFilter || strategyFilter || symbolQuery.trim() || pendingOnly || shadowFilter !== "all";

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
            className={`flex items-center gap-1.5 px-2 py-1 rounded text-xs font-mono font-medium transition-colors border ${
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
          <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded uppercase ${
            agentStatus?.autonomy_level?.toLowerCase() === "yolo"
              ? "bg-warning/15 text-warning"
              : agentStatus?.autonomy_level?.toLowerCase() === "semi"
                ? "bg-accent/15 text-accent"
                : "bg-bg-tertiary text-text-muted"
          }`}>
            {agentStatus?.autonomy_level ?? "semi"}
          </span>

          {/* Start/Stop button */}
          <button
            onClick={handleToggle}
            className={`px-2.5 py-1 rounded text-xs font-mono font-medium transition-colors ${
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
          <div className="text-[10px] text-text-muted font-mono uppercase mb-1">Status</div>
          <div className="flex items-center gap-1.5">
            <div
              className={`w-2 h-2 rounded-full ${
                agentStatus?.running ? "bg-profit animate-pulse" : "bg-text-muted"
              }`}
            />
            <span className="text-xs font-mono font-medium">
              {agentStatus?.running ? "RUNNING" : "STOPPED"}
            </span>
          </div>
        </div>
        <div className={`rounded border px-3 py-2 ${
          agentStatus?.yolo_mode ? "border-warning/30 bg-warning/5" : "border-border bg-bg-secondary"
        }`}>
          <div className="text-[10px] text-text-muted font-mono uppercase mb-1">Mode</div>
          <div className={`text-sm font-mono font-bold ${
            agentStatus?.yolo_mode ? "text-warning" : "text-accent"
          }`}>
            {agentStatus?.yolo_mode ? "YOLO" : "SEMI"}
          </div>
        </div>
        <div className="rounded border border-border bg-bg-secondary px-3 py-2">
          <div className="text-[10px] text-text-muted font-mono uppercase mb-1">Pending</div>
          <div className="text-sm font-mono font-bold">
            {agentStatus?.pending_confirmations ?? 0}
          </div>
        </div>
        <div className="rounded border border-border bg-bg-secondary px-3 py-2">
          <div className="text-[10px] text-text-muted font-mono uppercase mb-1">Monitored</div>
          <div className="text-sm font-mono font-bold">
            {agentStatus?.positions_monitored ?? 0}
          </div>
        </div>
        <div className="rounded border border-border bg-bg-secondary px-3 py-2">
          <div className="text-[10px] text-text-muted font-mono uppercase mb-1">Uptime</div>
          <div className="text-sm font-mono font-bold">
            {agentStatus?.uptime_seconds
              ? `${Math.floor(agentStatus.uptime_seconds / 60)}m`
              : "—"}
          </div>
        </div>
      </div>

      {/* Agent Logs */}
      <div className="rounded border border-border bg-bg-secondary">
        {/* Header: title + period filter */}
        <div className="px-3 py-1.5 border-b border-border flex items-center justify-between gap-3 flex-wrap">
          <h2 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
            Activity Log
          </h2>
          <PeriodFilter value={period} onChange={setPeriod} />
        </div>

        {/* Filter bar */}
        <div className="px-3 py-1.5 border-b border-border/60 flex items-center gap-2 flex-wrap">
          {/* Action type pills */}
          {actionTypes.length > 0 && (
            <div className="flex items-center gap-1 flex-wrap">
              <button
                onClick={() => setActionFilter("")}
                className={`text-[10px] font-mono px-1.5 py-px rounded border transition-colors ${
                  actionFilter === ""
                    ? "bg-accent/20 text-accent border-accent/30"
                    : "text-text-muted border-border hover:text-text-primary"
                }`}
              >
                ALL
              </button>
              {actionTypes.map((a) => (
                <button
                  key={a}
                  onClick={() => setActionFilter((prev) => (prev === a ? "" : a))}
                  className={`text-[10px] font-mono px-1.5 py-px rounded border transition-colors ${
                    actionFilter === a
                      ? "bg-accent/20 text-accent border-accent/30"
                      : `border-border hover:text-text-primary ${ACTION_COLORS[a] ?? "text-text-muted"}`
                  }`}
                >
                  {a.replace(/_/g, " ")}
                </button>
              ))}
            </div>
          )}

          {/* Divider */}
          {actionTypes.length > 0 && strategies.length > 0 && (
            <div className="w-px h-4 bg-border shrink-0" />
          )}

          {/* Strategy pills */}
          {strategies.length > 1 && (
            <div className="flex items-center gap-1">
              {strategies.map((s) => (
                <button
                  key={s}
                  onClick={() => setStrategyFilter((prev) => (prev === s ? "" : s))}
                  className={`text-[10px] font-mono px-1.5 py-px rounded border transition-colors ${
                    strategyFilter === s
                      ? "bg-accent/20 text-accent border-accent/30"
                      : "text-text-muted border-border hover:text-text-primary"
                  }`}
                >
                  {STRATEGY_LABELS[s] ?? s}
                </button>
              ))}
            </div>
          )}

          {/* Shadow / Real filter */}
          <div className="flex items-center rounded border border-border overflow-hidden text-[10px] font-mono">
            <button
              onClick={() => setShadowFilter("all")}
              className={`px-1.5 py-0.5 transition-colors ${
                shadowFilter === "all" ? "bg-accent/15 text-accent" : "text-text-muted hover:text-text-secondary"
              }`}
            >
              All
            </button>
            <button
              onClick={() => setShadowFilter("real")}
              className={`px-1.5 py-0.5 border-l border-border transition-colors ${
                shadowFilter === "real" ? "bg-accent/15 text-accent" : "text-text-muted hover:text-text-secondary"
              }`}
            >
              Real
            </button>
            <button
              onClick={() => setShadowFilter("shadow")}
              className={`px-1.5 py-0.5 border-l border-border transition-colors ${
                shadowFilter === "shadow" ? "bg-purple-500/15 text-purple-400" : "text-text-muted hover:text-text-secondary"
              }`}
            >
              Shadow
            </button>
          </div>

          {/* Symbol search */}
          <input
            type="text"
            value={symbolQuery}
            onChange={(e) => setSymbolQuery(e.target.value)}
            placeholder="symbol…"
            className="ml-auto text-[10px] font-mono bg-bg-tertiary border border-border rounded px-1.5 py-px text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent/50 w-20"
          />

          {/* Pending only */}
          <label className="flex items-center gap-1 cursor-pointer select-none shrink-0">
            <input
              type="checkbox"
              checked={pendingOnly}
              onChange={(e) => setPendingOnly(e.target.checked)}
              className="accent-accent w-3 h-3"
            />
            <span className="text-[10px] font-mono text-text-muted">Pending</span>
          </label>

          {/* Count */}
          {!loading && (
            <span className="text-[10px] font-mono text-text-muted shrink-0">
              {hasActiveFilters ? `${displayed.length} / ${logs.length}` : `${logs.length}`}
            </span>
          )}
        </div>

        {/* Log rows */}
        {loading ? (
          <div className="px-3 py-6 text-center text-text-muted text-xs font-mono">loading...</div>
        ) : displayed.length === 0 ? (
          <div className="px-3 py-6 text-center text-text-muted text-xs font-mono">
            {hasActiveFilters ? "no matching entries" : "no activity yet"}
          </div>
        ) : (
          <div className="divide-y divide-border/30 max-h-[calc(100vh-340px)] overflow-y-auto">
            {displayed.map((log) => {
              const symbol = log.details?.symbol != null ? String(log.details.symbol) : null;
              const strategyKey = log.details?.strategy_name as string | undefined;
              const pnl = log.details?.pnl != null ? Number(log.details.pnl) : null;
              const shadow = log.action_type === "SHADOW_EXECUTED" || log.details?.is_shadow === true;

              return (
                <div key={log.id} className="px-3 py-1.5 hover:bg-bg-tertiary/30">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2 flex-wrap">
                      {shadow && (
                        <span className="text-[9px] font-mono px-1 py-px rounded bg-purple-500/15 text-purple-400 shrink-0">
                          SHADOW
                        </span>
                      )}
                      <span
                        className={`text-xs font-mono font-bold ${
                          ACTION_COLORS[log.action_type] ?? "text-text-secondary"
                        }`}
                      >
                        {log.action_type.replace(/_/g, " ")}
                      </span>
                      {symbol && (
                        <span className="text-xs font-mono text-text-primary">{symbol}</span>
                      )}
                      {strategyKey && (
                        <span className="text-[10px] font-mono px-1 py-px rounded bg-accent/10 text-accent">
                          {STRATEGY_LABELS[strategyKey] ?? strategyKey}
                        </span>
                      )}
                      {pnl != null && (
                        <span className={`text-xs font-mono ${pnl >= 0 ? "text-profit" : "text-loss"}`}>
                          {pnl >= 0 ? "+" : ""}{pnl.toFixed(0)}
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] text-text-muted font-mono shrink-0">
                      {formatDate(log.created_at)} {formatTime(log.created_at)}
                    </span>
                  </div>

                  {log.details?.message != null && (
                    <p className="text-[10px] font-mono text-text-muted mt-0.5 leading-relaxed">
                      {String(log.details.message)}
                    </p>
                  )}

                  {log.requires_confirmation && log.confirmation_status === "PENDING" && (
                    <div className="mt-1 flex gap-1.5">
                      <button
                        onClick={() => handleConfirm(log.id, true)}
                        className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-profit/15 text-profit hover:bg-profit/25"
                      >
                        APPROVE
                      </button>
                      <button
                        onClick={() => handleConfirm(log.id, false)}
                        className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-loss/15 text-loss hover:bg-loss/25"
                      >
                        REJECT
                      </button>
                    </div>
                  )}

                  {log.confirmation_status && log.confirmation_status !== "PENDING" && (
                    <span
                      className={`text-[10px] font-mono mt-0.5 inline-block ${
                        log.confirmation_status === "APPROVED" ? "text-profit" : "text-loss"
                      }`}
                    >
                      {log.confirmation_status}
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

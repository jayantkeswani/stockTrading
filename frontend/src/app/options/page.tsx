"use client";

import { useEffect, useState } from "react";
import { AgentLog } from "@/components/options/AgentLog";
import { api } from "@/lib/api";
import { isoDateIST } from "@/lib/formatters";

type DateMode = "today" | "custom";

const WINDOW_COLORS: Record<string, { label: string; cls: string }> = {
  IN_WINDOW:     { label: "IN WINDOW",  cls: "bg-profit/20 text-profit" },
  DEAD_ZONE:     { label: "DEAD ZONE",  cls: "bg-warning/20 text-warning" },
  OUT_OF_WINDOW: { label: "OFF WINDOW", cls: "bg-text-muted/20 text-text-muted" },
};

export default function OptionsPage() {
  const [mode, setMode] = useState<DateMode>("today");
  const [customDate, setCustomDate] = useState(() => isoDateIST(new Date()));
  const [windowState, setWindowState] = useState<string | null>(null);

  const agentLogDate = mode === "today" ? null : customDate;
  const isLive = mode === "today";

  useEffect(() => {
    if (!isLive) {
      setWindowState(null);
      return;
    }
    const fetch = async () => {
      try {
        const res = await api.getOptionsWindowState();
        setWindowState(res.window_state);
      } catch { /* silent */ }
    };
    fetch();
    const interval = setInterval(fetch, 5000);
    return () => clearInterval(interval);
  }, [isLive]);

  const wCfg = windowState ? WINDOW_COLORS[windowState] ?? WINDOW_COLORS["OUT_OF_WINDOW"] : null;

  function selectToday() {
    setMode("today");
  }

  function selectCustom() {
    setMode("custom");
  }

  return (
    <div className="space-y-3">
      {/* Status bar — mirrors DayStatusBar pattern from Futures page */}
      <div className="flex items-center gap-3 px-3 py-1.5 bg-bg-secondary border border-border rounded">
        <h1 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          Options — VWAP Pullback
        </h1>

        <div className="w-px h-4 bg-border" />

        {/* Window state badge */}
        {isLive && wCfg && (
          <span className={`text-[10px] font-mono px-1.5 py-px rounded ${wCfg.cls}`}>
            {wCfg.label}
          </span>
        )}

        <div className="flex-1" />

        {/* Date controls */}
        <div className="flex items-center gap-1.5">
          <button
            onClick={selectToday}
            className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-medium transition-all duration-150 border ${
              mode === "today"
                ? "bg-accent/20 text-accent border-accent/30"
                : "text-text-muted border-transparent hover:text-text-secondary hover:bg-bg-tertiary"
            }`}
          >
            Today
          </button>
          <button
            onClick={selectCustom}
            className={`inline-flex items-center px-2 py-0.5 rounded text-xs font-mono font-medium transition-all duration-150 border ${
              mode === "custom"
                ? "bg-accent/20 text-accent border-accent/30"
                : "text-text-muted border-transparent hover:text-text-secondary hover:bg-bg-tertiary"
            }`}
          >
            Custom
          </button>
          <input
            type="date"
            value={customDate}
            onChange={(e) => { setCustomDate(e.target.value); setMode("custom"); }}
            className={`bg-bg-secondary border rounded px-1.5 py-0.5 text-xs font-mono focus:outline-none ml-1 transition-opacity duration-150 ${
              mode === "custom"
                ? "border-accent/30 text-text-primary focus:border-accent/50 opacity-100"
                : "border-transparent text-transparent opacity-0 pointer-events-none"
            }`}
          />
        </div>
      </div>

      <div className="grid grid-cols-12 gap-3">
        <div className="col-span-8">
          <AgentLog date={agentLogDate} />
        </div>
      </div>
    </div>
  );
}

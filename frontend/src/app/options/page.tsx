"use client";

import { useState } from "react";
import { AgentLog } from "@/components/options/AgentLog";
import { isoDateIST } from "@/lib/formatters";

type DateMode = "today" | "custom";

export default function OptionsPage() {
  const [mode, setMode] = useState<DateMode>("today");
  const [customDate, setCustomDate] = useState(() => isoDateIST(new Date()));

  const agentLogDate = mode === "today" ? null : customDate;

  function selectToday() {
    setMode("today");
  }

  function selectCustom() {
    setMode("custom");
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h1 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          Options — VWAP Pullback
        </h1>
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

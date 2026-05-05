"use client";

import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { S5AgentLogEntry } from "@/lib/types";

const CATEGORY_COLORS: Record<string, string> = {
  BRIEFING: "bg-accent/20 text-accent",
  SCREENER: "bg-accent/20 text-accent",
  ORB: "bg-warning/20 text-warning",
  SIGNAL: "bg-profit/20 text-profit",
  TRADE: "bg-profit/20 text-profit",
  EXIT: "bg-loss/20 text-loss",
  SKIP: "bg-text-muted/20 text-text-muted",
  PHASE: "bg-[#7c6aef]/20 text-[#7c6aef]",
  RISK: "bg-loss/20 text-loss",
  GLOBAL: "bg-accent/20 text-accent",
  SYSTEM: "bg-text-muted/20 text-text-muted",
};

function formatTime(ts: number): string {
  try {
    const d = new Date(ts * 1000);
    return d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false, timeZone: "Asia/Kolkata" });
  } catch {
    return String(ts);
  }
}

export function AgentLog({ date }: { date: string | null }) {
  const [entries, setEntries] = useState<S5AgentLogEntry[]>([]);
  const [activeCategories, setActiveCategories] = useState<Set<string>>(new Set());

  const isHistorical = date != null;

  const fetchLog = async () => {
    try {
      const data = await api.getIntradayFuturesAgentLog(date ?? undefined);
      setEntries([...data].reverse());
    } catch {
      /* silent */
    }
  };

  useEffect(() => {
    fetchLog();
    if (isHistorical) return;
    const interval = setInterval(fetchLog, 10_000);
    return () => clearInterval(interval);
  }, [date]);

  const uniqueCategories = [...new Set(entries.map((e) => e.category))].sort();
  const filtered =
    activeCategories.size === 0
      ? entries
      : entries.filter((e) => activeCategories.has(e.category));

  const toggleCategory = (cat: string) => {
    setActiveCategories((prev) => {
      const next = new Set(prev);
      if (next.has(cat)) next.delete(cat);
      else next.add(cat);
      return next;
    });
  };

  return (
    <div className="border border-border rounded bg-bg-secondary">
      <div className="px-3 py-1.5 border-b border-border space-y-1.5">
        <div className="flex items-center justify-between">
          <span className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
            Agent Log
            {activeCategories.size > 0 ? (
              <span className="text-text-muted ml-1">({filtered.length}/{entries.length})</span>
            ) : (
              <span className="text-text-muted ml-1">({entries.length})</span>
            )}
          </span>
          {activeCategories.size > 0 && (
            <button
              onClick={() => setActiveCategories(new Set())}
              className="text-[9px] font-mono text-text-muted hover:text-accent transition-colors"
            >
              clear
            </button>
          )}
        </div>
        {uniqueCategories.length > 1 && (
          <div className="flex flex-wrap gap-1">
            {uniqueCategories.map((cat) => {
              const isActive = activeCategories.has(cat);
              return (
                <button
                  key={cat}
                  onClick={() => toggleCategory(cat)}
                  className={`text-[9px] font-mono px-1.5 py-px rounded transition-colors ${
                    isActive
                      ? CATEGORY_COLORS[cat] || "bg-bg-tertiary text-text-muted"
                      : "bg-bg-tertiary/50 text-text-muted hover:text-text-secondary"
                  }`}
                >
                  {cat}
                </button>
              );
            })}
          </div>
        )}
      </div>
      <div className="max-h-[400px] overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="px-3 py-4 text-center text-text-muted text-xs font-mono">
            {entries.length === 0 ? "no agent activity today" : "no matching entries"}
          </div>
        ) : (
          <div className="divide-y divide-border/30">
            {filtered.map((entry, i) => (
              <div key={i} className="px-3 py-1.5 hover:bg-bg-tertiary">
                <div className="flex items-start gap-2">
                  <span className="text-[10px] font-mono text-text-muted shrink-0 pt-px">
                    {formatTime(entry.timestamp)}
                  </span>
                  <span className={`text-[10px] font-mono px-1 py-px rounded shrink-0 ${CATEGORY_COLORS[entry.category] || "bg-bg-tertiary text-text-muted"}`}>
                    {entry.category}
                  </span>
                  <span className="text-xs font-mono text-text-secondary break-all">
                    {entry.message}
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

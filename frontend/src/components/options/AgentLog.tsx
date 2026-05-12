"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { api } from "@/lib/api";
import type { S5AgentLogEntry } from "@/lib/types";

const PAGE_SIZE = 100;

const CATEGORY_COLORS: Record<string, string> = {
  GATE: "bg-loss/20 text-loss",
  SIGNAL: "bg-profit/20 text-profit",
  SKIP: "bg-text-muted/20 text-text-muted",
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
  const [total, setTotal] = useState(0);
  const [activeCategories, setActiveCategories] = useState<Set<string>>(new Set());
  const [loadingMore, setLoadingMore] = useState(false);
  const sentinelRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const isHistorical = date != null;
  const hasMore = entries.length < total;

  const fetchPage = useCallback(async (offset: number, append: boolean) => {
    try {
      const data = await api.getOptionsAgentLog(date ?? undefined, offset, PAGE_SIZE);
      setTotal(data.total);
      setEntries((prev) => append ? [...prev, ...data.entries] : data.entries);
    } catch { /* silent */ }
  }, [date]);

  useEffect(() => {
    setEntries([]);
    setTotal(0);
    setActiveCategories(new Set());
    fetchPage(0, false);
    if (isHistorical) return;
    const interval = setInterval(() => fetchPage(0, false), 10_000);
    return () => clearInterval(interval);
  }, [date, fetchPage, isHistorical]);

  const loadMore = useCallback(async () => {
    if (loadingMore || !hasMore) return;
    setLoadingMore(true);
    await fetchPage(entries.length, true);
    setLoadingMore(false);
  }, [loadingMore, hasMore, entries.length, fetchPage]);

  useEffect(() => {
    const sentinel = sentinelRef.current;
    const container = scrollRef.current;
    if (!sentinel || !container) return;
    const observer = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) loadMore(); },
      { root: container, rootMargin: "100px" },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [loadMore]);

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
              <span className="text-text-muted ml-1">({filtered.length}/{total})</span>
            ) : (
              <span className="text-text-muted ml-1">({entries.length}/{total})</span>
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
      <div ref={scrollRef} className="max-h-[400px] overflow-y-auto">
        {filtered.length === 0 ? (
          <div className="px-3 py-4 text-center text-text-muted text-xs font-mono">
            {entries.length === 0 ? "no agent activity today" : "no matching entries"}
          </div>
        ) : (
          <div className="divide-y divide-border/30">
            {filtered.map((entry, i) => (
              <div key={`${entry.timestamp}-${entry.category}-${i}`} className="px-3 py-1.5 hover:bg-bg-tertiary">
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
            <div ref={sentinelRef} className="h-1" />
          </div>
        )}
        {loadingMore && (
          <div className="px-3 py-2 text-center text-text-muted text-[10px] font-mono">
            loading…
          </div>
        )}
      </div>
    </div>
  );
}

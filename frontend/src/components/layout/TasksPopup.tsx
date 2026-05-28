"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { api } from "@/lib/api";
import type { BackgroundTask } from "@/lib/types";

const STATUS_DOT: Record<string, string> = {
  running: "bg-profit",
  completed: "bg-profit/60",
  failed: "bg-loss",
  pending: "bg-warning",
  stopped: "bg-text-muted",
};

const STATUS_LABEL: Record<string, string> = {
  running: "text-profit",
  completed: "text-text-muted",
  failed: "text-loss",
  pending: "text-warning",
  stopped: "text-text-muted",
};

const TYPE_BADGE: Record<string, string> = {
  scheduler: "bg-accent/15 text-accent",
  startup: "bg-[#6366f1]/15 text-[#818cf8]",
  service: "bg-profit/15 text-profit",
};

function formatName(name: string): string {
  return name.replace(/_/g, " ");
}

function formatTime(iso: string | null): string {
  if (!iso) return "-";
  return new Date(iso).toLocaleTimeString("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function TasksPopup() {
  const [open, setOpen] = useState(false);
  const [tasks, setTasks] = useState<BackgroundTask[]>([]);
  const ref = useRef<HTMLDivElement>(null);

  const fetchTasks = useCallback(async () => {
    try {
      const { tasks: t } = await api.getTasks();
      setTasks(t ?? []);
    } catch {
      // backend not available
    }
  }, []);

  // Fetch once on mount so the dot color is accurate before popup is opened
  useEffect(() => {
    fetchTasks();
  }, [fetchTasks]);

  // Fetch on open, then poll every 5s while open
  useEffect(() => {
    if (!open) return;
    fetchTasks();
    const interval = setInterval(fetchTasks, 5000);
    return () => clearInterval(interval);
  }, [open, fetchTasks]);

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

  const running = tasks.filter((t) => t.status === "running").length;
  const failed = tasks.filter((t) => t.status === "failed").length;

  const dotColor = failed > 0 ? "bg-loss" : running > 0 ? "bg-profit" : "bg-text-muted";

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1 hover:opacity-80 transition-opacity"
      >
        <div className={`w-1.5 h-1.5 rounded-full ${dotColor}`} />
        <span className="text-[10px] font-mono text-text-muted">
          TASKS
          {failed > 0 && <span className="text-loss ml-0.5">{failed}!</span>}
        </span>
      </button>

      {open && (
        <div className="absolute top-6 right-0 w-80 bg-bg-elevated border border-border rounded shadow-lg z-50 animate-fade-in">
          {/* Header */}
          <div className="flex items-center justify-between px-3 py-1.5 border-b border-border">
            <span className="text-[10px] font-mono font-medium text-text-secondary uppercase tracking-wider">
              background tasks
            </span>
            <span className="text-[9px] font-mono text-text-muted">
              {running} running / {tasks.length} total
            </span>
          </div>

          {/* Task list */}
          <div className="max-h-72 overflow-y-auto">
            {tasks.length === 0 ? (
              <div className="px-3 py-4 text-center text-[10px] font-mono text-text-muted">
                no tasks registered
              </div>
            ) : (
              tasks.map((task) => (
                <div
                  key={task.name}
                  className="px-3 py-1.5 border-b border-border/50 last:border-b-0 hover:bg-bg-tertiary/50"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5">
                      <div className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${STATUS_DOT[task.status] || "bg-text-muted"}`} />
                      <span className="text-[10px] font-mono text-text-primary truncate max-w-[140px]">
                        {formatName(task.name)}
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <span className={`text-[8px] font-mono px-1 py-px rounded ${TYPE_BADGE[task.type] || ""}`}>
                        {task.type}
                      </span>
                      <span className={`text-[9px] font-mono ${STATUS_LABEL[task.status] || "text-text-muted"}`}>
                        {task.status}
                      </span>
                    </div>
                  </div>

                  {/* Meta line */}
                  <div className="flex items-center gap-2 mt-0.5 ml-3">
                    {task.metadata?.description && (
                      <span className="text-[8px] font-mono text-text-muted truncate max-w-[160px]">
                        {task.metadata.description}
                      </span>
                    )}
                    {task.metadata?.schedule && (
                      <span className="text-[8px] font-mono text-text-muted truncate">
                        {task.metadata.schedule}
                      </span>
                    )}
                    {task.started_at && (
                      <span className="text-[8px] font-mono text-text-muted ml-auto flex-shrink-0">
                        {formatTime(task.started_at)}
                      </span>
                    )}
                  </div>

                  {/* Error */}
                  {task.error && (
                    <div className="mt-0.5 ml-3 text-[8px] font-mono text-loss truncate" title={task.error}>
                      {task.error}
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

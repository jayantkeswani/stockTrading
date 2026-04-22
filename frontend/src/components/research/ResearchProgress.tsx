"use client";

import type { ResearchAgentStatus } from "@/lib/types";

const STATUS_COLORS: Record<string, string> = {
  pending: "bg-text-muted",
  running: "bg-accent animate-pulse",
  completed: "bg-[#00e68a]",
  failed: "bg-[#ff4060]",
};

const AGENT_LABELS: Record<string, string> = {
  fundamental: "Fundamental",
  technical: "Technical",
  oi_derivatives: "OI & Derivatives",
  institutional: "Institutional",
  news_sentiment: "News & Sentiment",
  valuation: "Valuation",
};

export function ResearchProgress({
  agents,
  symbol,
}: {
  agents: ResearchAgentStatus[];
  symbol: string;
}) {
  const completed = agents.filter((a) => a.status === "completed").length;
  const total = agents.length;

  return (
    <div className="border border-border rounded bg-bg-secondary p-3">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          Researching {symbol}
        </span>
        <span className="text-[10px] font-mono text-text-muted">
          {completed}/{total} agents
        </span>
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
        {agents.map((agent) => (
          <div
            key={agent.name}
            className="flex items-center gap-1.5 text-[11px] font-mono"
          >
            <span
              className={`w-1.5 h-1.5 rounded-full flex-shrink-0 ${STATUS_COLORS[agent.status] || "bg-text-muted"}`}
            />
            <span
              className={
                agent.status === "completed"
                  ? "text-text-primary"
                  : agent.status === "running"
                    ? "text-accent"
                    : agent.status === "failed"
                      ? "text-[#ff4060]"
                      : "text-text-muted"
              }
            >
              {AGENT_LABELS[agent.name] || agent.name}
            </span>
            {agent.duration !== undefined && agent.status === "completed" && (
              <span className="text-text-muted">{agent.duration.toFixed(1)}s</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

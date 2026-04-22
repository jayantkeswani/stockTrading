"use client";

import { useState } from "react";
import type { ResearchReport as ResearchReportType } from "@/lib/types";
import { formatINR } from "@/lib/formatters";
import { RecommendationBadge } from "./RecommendationBadge";
import { ActionableLevels } from "./ActionableLevels";

const SECTION_LABELS: Record<string, string> = {
  fundamental: "Fundamental Analysis",
  technical: "Technical Analysis",
  oi_derivatives: "OI & Derivatives",
  institutional: "Institutional Activity",
  news_sentiment: "News & Sentiment",
  valuation: "Valuation",
};

const SECTION_ORDER = [
  "fundamental",
  "technical",
  "oi_derivatives",
  "institutional",
  "news_sentiment",
  "valuation",
];

export function ResearchReport({ report }: { report: ResearchReportType }) {
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set());

  const toggleSection = (name: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const reportJson = report.report_json || {};
  const shortTerm = (reportJson.short_term_opportunity as Record<string, unknown>) || null;
  const longTerm = reportJson.long_term_outlook as Record<string, unknown> | undefined;
  const sectionVerdicts = (reportJson.section_verdicts as Record<string, string>) || {};

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="border border-border rounded bg-bg-secondary p-3">
        <div className="flex items-center justify-between mb-2">
          <div>
            <h2 className="text-sm font-mono font-medium text-text-primary">
              {report.symbol}
              <span className="text-text-muted font-normal ml-2">
                {report.display_name}
              </span>
            </h2>
            <div className="flex items-center gap-3 mt-1">
              {report.price_at_research && (
                <span className="text-xs font-mono text-text-secondary">
                  Rs {formatINR(report.price_at_research)}
                </span>
              )}
              {report.market_cap_cr && (
                <span className="text-xs font-mono text-text-muted">
                  MCap: {report.market_cap_cr >= 100
                    ? `${(report.market_cap_cr / 100).toFixed(1)}L Cr`
                    : `${report.market_cap_cr.toFixed(0)} Cr`}
                </span>
              )}
              {report.duration_seconds && (
                <span className="text-[10px] font-mono text-text-muted">
                  {report.duration_seconds.toFixed(1)}s
                </span>
              )}
            </div>
          </div>
          <RecommendationBadge
            recommendation={report.recommendation}
            confidence={report.confidence_score}
          />
        </div>

        {/* Executive Summary */}
        {report.executive_summary && (
          <div className="mt-3 pt-3 border-t border-border">
            <div className="text-[10px] font-mono text-text-muted uppercase tracking-wider mb-1">
              Executive Summary
            </div>
            <p className="text-xs font-mono text-text-secondary leading-relaxed">
              {report.executive_summary}
            </p>
          </div>
        )}
      </div>

      {/* Actionable Levels */}
      <ActionableLevels
        shortTerm={shortTerm as never}
        longTermSuitability={longTerm?.suitability as string}
      />

      {/* Risks & Catalysts */}
      {(Array.isArray(reportJson.key_risks) || Array.isArray(reportJson.key_catalysts)) && (
        <div className="grid grid-cols-2 gap-3">
          {Array.isArray(reportJson.key_risks) && (reportJson.key_risks as string[]).length > 0 && (
            <div className="border border-border rounded bg-bg-secondary p-2.5">
              <div className="text-[10px] font-mono text-[#ff4060] uppercase tracking-wider mb-1">
                Key Risks
              </div>
              {(reportJson.key_risks as string[]).map((r: string, i: number) => (
                <div key={i} className="text-[11px] font-mono text-text-secondary leading-relaxed">
                  {i + 1}. {r}
                </div>
              ))}
            </div>
          )}
          {Array.isArray(reportJson.key_catalysts) && (reportJson.key_catalysts as string[]).length > 0 && (
            <div className="border border-border rounded bg-bg-secondary p-2.5">
              <div className="text-[10px] font-mono text-[#00e68a] uppercase tracking-wider mb-1">
                Key Catalysts
              </div>
              {(reportJson.key_catalysts as string[]).map((c: string, i: number) => (
                <div key={i} className="text-[11px] font-mono text-text-secondary leading-relaxed">
                  {i + 1}. {c}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Expandable Section Cards */}
      <div className="space-y-1">
        {SECTION_ORDER.map((name) => {
          const agentRun = report.agent_runs.find((r) => r.agent_name === name);
          const verdict = sectionVerdicts[name];
          const isExpanded = expandedSections.has(name);
          const isFailed = agentRun?.status === "FAILED";

          return (
            <div key={name} className="border border-border rounded bg-bg-secondary">
              <button
                onClick={() => toggleSection(name)}
                className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-bg-tertiary transition-colors"
              >
                <div className="flex items-center gap-2">
                  <span className="text-[10px] text-text-muted">
                    {isExpanded ? "▾" : "▸"}
                  </span>
                  <span className="text-xs font-mono font-medium text-text-primary">
                    {SECTION_LABELS[name] || name}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  {verdict && !isFailed && (
                    <span className="text-[10px] font-mono text-text-secondary max-w-[200px] truncate">
                      {verdict}
                    </span>
                  )}
                  {isFailed && (
                    <span className="text-[10px] font-mono text-[#ff4060]">
                      failed
                    </span>
                  )}
                  {agentRun?.duration_seconds && (
                    <span className="text-[10px] font-mono text-text-muted">
                      {agentRun.duration_seconds.toFixed(1)}s
                    </span>
                  )}
                </div>
              </button>

              {isExpanded && agentRun && (
                <div className="px-3 pb-3 border-t border-border animate-fade-in">
                  {agentRun.summary_text && (
                    <p className="text-[11px] font-mono text-text-secondary leading-relaxed mt-2 whitespace-pre-wrap">
                      {agentRun.summary_text}
                    </p>
                  )}

                  {agentRun.error_message && (
                    <p className="text-[11px] font-mono text-[#ff4060] mt-2">
                      Error: {agentRun.error_message}
                    </p>
                  )}

                  {name === "news_sentiment" && Array.isArray((agentRun.findings_json as Record<string, unknown> | null)?.articles) && (
                    <div className="mt-2 space-y-1">
                      <div className="text-[10px] font-mono text-text-muted uppercase tracking-wider">
                        Recent Articles
                      </div>
                      {((agentRun.findings_json as Record<string, unknown>)?.articles as Array<Record<string, string>>).slice(0, 8).map((article, i) => (
                        <div key={i} className="flex items-start gap-1.5 text-[10px] font-mono">
                          <span className={
                            article.sentiment === "positive" ? "text-[#00e68a]" :
                            article.sentiment === "negative" ? "text-[#ff4060]" :
                            "text-text-muted"
                          }>
                            {article.sentiment === "positive" ? "+" :
                             article.sentiment === "negative" ? "-" : "~"}
                          </span>
                          {article.url ? (
                            <a
                              href={article.url}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-text-secondary hover:text-accent underline-offset-2 hover:underline"
                            >
                              {article.headline}
                            </a>
                          ) : (
                            <span className="text-text-secondary">{article.headline}</span>
                          )}
                          {article.source && (
                            <span className="text-text-muted flex-shrink-0">
                              — {article.source}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}

                  {agentRun.data_sources_used && agentRun.data_sources_used.length > 0 && (
                    <div className="mt-2 text-[10px] font-mono text-text-muted">
                      Sources: {agentRun.data_sources_used.join(", ")}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

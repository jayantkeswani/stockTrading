"use client";

import { useEffect, useState, useCallback } from "react";
import { useStore } from "@/store";
import { api } from "@/lib/api";
import type { ResearchReport as ResearchReportType, ResearchReportListItem } from "@/lib/types";
import { ResearchSearch } from "@/components/research/ResearchSearch";
import { ResearchProgress } from "@/components/research/ResearchProgress";
import { ResearchReport } from "@/components/research/ResearchReport";
import { ReportHistory } from "@/components/research/ReportHistory";

export default function ResearchPage() {
  const {
    activeResearches,
    selectedResearchId,
    setSelectedResearchId,
    researchReports,
    setResearchReports,
    startResearchSession,
  } = useStore();

  const [fullReport, setFullReport] = useState<ResearchReportType | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Load past reports on mount
  useEffect(() => {
    async function loadHistory() {
      try {
        const reports = await api.getResearchReports({ limit: 20 });
        setResearchReports(reports);
      } catch {
        // API not ready
      }
    }
    loadHistory();
  }, [setResearchReports]);

  // Load full report when selectedResearchId changes and research is NOT in-progress
  useEffect(() => {
    if (!selectedResearchId) {
      setFullReport(null);
      return;
    }

    const activeResearch = activeResearches[selectedResearchId];
    if (activeResearch && activeResearch.status === "in_progress") {
      // Still running - don't fetch yet
      return;
    }

    // Fetch the full report (with small delay to let DB commit finish)
    const timer = setTimeout(async () => {
      try {
        const report = await api.getResearchReport(selectedResearchId);
        setFullReport(report);
      } catch {
        // Report may not be ready yet
      }
      // Also refresh history
      try {
        const reports = await api.getResearchReports({ limit: 20 });
        setResearchReports(reports);
      } catch {
        // ignore
      }
    }, activeResearch ? 500 : 0); // 500ms delay if just completed, instant for history clicks

    return () => clearTimeout(timer);
  }, [selectedResearchId, activeResearches, setResearchReports]);

  const handleStartResearch = useCallback(
    async (symbol: string) => {
      setError(null);
      setLoading(true);
      try {
        const result = await api.startResearch(symbol);
        // The WebSocket handler will call startResearchSession
        // but we also call it here for immediate UI feedback
        startResearchSession(result.report_id, symbol, result.agents_total);
        setSelectedResearchId(result.report_id);
        setFullReport(null);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to start research");
      }
      setLoading(false);
    },
    [startResearchSession, setSelectedResearchId]
  );

  const handleSelectReport = useCallback(
    (id: string) => {
      setSelectedResearchId(id);
      setFullReport(null);
      api.getResearchReport(id).then(setFullReport).catch(() => {});
    },
    [setSelectedResearchId]
  );

  const handleCloseReport = useCallback(() => {
    setFullReport(null);
    setSelectedResearchId(null);
  }, [setSelectedResearchId]);

  // Escape key to close report
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape" && fullReport) handleCloseReport();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [fullReport, handleCloseReport]);

  const handleDeleteReport = useCallback(
    async (id: string) => {
      try {
        await api.deleteResearchReport(id);
        // Remove from history list
        setResearchReports(researchReports.filter((r) => r.id !== id));
        // Clear selection if this was the selected report
        if (selectedResearchId === id) {
          setSelectedResearchId(null);
          setFullReport(null);
        }
      } catch {
        // Ignore delete errors
      }
    },
    [researchReports, selectedResearchId, setResearchReports, setSelectedResearchId]
  );

  // Current active research for progress display
  const activeResearch = selectedResearchId
    ? activeResearches[selectedResearchId]
    : null;
  const isResearching = activeResearch?.status === "in_progress";

  return (
    <div className="space-y-3">
      {/* Search bar */}
      <ResearchSearch
        onStartResearch={handleStartResearch}
        isResearching={loading || !!isResearching}
      />

      {error && (
        <div className="text-xs font-mono text-[#ff4060] bg-[#ff4060]/10 border border-[#ff4060]/20 rounded px-3 py-1.5">
          {error}
        </div>
      )}

      {/* Progress indicator */}
      {isResearching && activeResearch && (
        <ResearchProgress
          agents={activeResearch.agentStatuses}
          symbol={activeResearch.symbol}
        />
      )}

      {/* Full report */}
      {fullReport && (
        <ResearchReport report={fullReport} onClose={handleCloseReport} />
      )}

      {/* Empty state */}
      {!isResearching && !fullReport && !loading && (
        <div className="border border-border rounded bg-bg-secondary p-8 text-center">
          <div className="text-text-muted text-xs font-mono">
            search for a stock to start research
          </div>
          <div className="text-text-muted text-[10px] font-mono mt-1">
            our ai agents will analyze fundamentals, technicals, oi, news, and more
          </div>
        </div>
      )}

      {/* Report History */}
      {researchReports.length > 0 && (
        <div className="border border-border rounded bg-bg-secondary p-3">
          <div className="text-[10px] font-mono text-text-secondary uppercase tracking-wider mb-2 font-medium">
            Past Reports
          </div>
          <ReportHistory
            reports={researchReports}
            selectedId={selectedResearchId}
            onSelect={handleSelectReport}
            onDelete={handleDeleteReport}
          />
        </div>
      )}
    </div>
  );
}

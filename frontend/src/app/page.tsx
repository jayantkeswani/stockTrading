"use client";

import { useState, useCallback } from "react";
import { ScannerPanel } from "@/components/dashboard/ScannerPanel";
import { ActivePositions } from "@/components/positions/ActivePositions";
import { PnLCard } from "@/components/dashboard/PnLCard";
import { Watchlist } from "@/components/dashboard/Watchlist";
import { AgentFeed } from "@/components/dashboard/AgentFeed";
import { ChartModal } from "@/components/charts/ChartModal";
import { useStore } from "@/store";

export default function DashboardPage() {
  const [chartOpen, setChartOpen] = useState(false);
  const [chartSymbol, setChartSymbol] = useState<string | undefined>(undefined);
  const { setSelectedSymbol } = useStore();

  const handleOpenChart = useCallback(
    (symbol?: string) => {
      if (symbol) {
        setSelectedSymbol(symbol);
        setChartSymbol(symbol);
      }
      setChartOpen(true);
    },
    [setSelectedSymbol]
  );

  const handleCloseChart = useCallback(() => {
    setChartOpen(false);
  }, []);

  return (
    <>
      <div className="grid grid-cols-10 gap-4 h-[calc(100vh-80px)]">
        {/* LEFT: Main content (70%) */}
        <div className="col-span-7 flex flex-col gap-4 overflow-y-auto pr-1 pb-4">
          {/* Scanner Panel */}
          <ScannerPanel />

          {/* Active Positions */}
          <ActivePositions compact />

          {/* P&L + Risk Row */}
          <PnLCard />

          {/* Open Chart Button */}
          <button
            onClick={() => handleOpenChart()}
            className="flex items-center justify-center gap-2 py-2.5 rounded-lg border border-border bg-bg-secondary text-text-secondary hover:text-text-primary hover:border-border-hover transition-colors"
          >
            <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M7 12l3-3 3 3 4-4M8 21l4-4 4 4M3 4h18M4 4h16v12a1 1 0 01-1 1H5a1 1 0 01-1-1V4z" />
            </svg>
            <span className="text-sm font-medium">Open Chart</span>
          </button>
        </div>

        {/* RIGHT: Sidebar (30%) */}
        <div className="col-span-3 flex flex-col gap-4 overflow-y-auto pb-4">
          {/* Watchlist */}
          <Watchlist onOpenChart={handleOpenChart} />

          {/* Agent Feed */}
          <AgentFeed />
        </div>
      </div>

      {/* Chart Modal */}
      <ChartModal
        isOpen={chartOpen}
        onClose={handleCloseChart}
        initialSymbol={chartSymbol}
      />
    </>
  );
}

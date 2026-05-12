"use client";

import { useState, useCallback } from "react";
import { DayStatusBar } from "@/components/intraday-futures/DayStatusBar";
import { Watchlist } from "@/components/intraday-futures/Watchlist";
import { AgentLog } from "@/components/intraday-futures/AgentLog";
import { GlobalCues } from "@/components/intraday-futures/GlobalCues";
import { SetupPerformance } from "@/components/intraday-futures/SetupPerformance";
import { ConfigPanel } from "@/components/intraday-futures/ConfigPanel";
import { PermanentWatchlist } from "@/components/intraday-futures/PermanentWatchlist";
import { ChartModal } from "@/components/charts/ChartModal";
import { useStore } from "@/store";

export default function IntradayFuturesPage() {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);
  const [briefingKey, setBriefingKey] = useState(0);
  const [chartOpen, setChartOpen] = useState(false);
  const [chartSymbol, setChartSymbol] = useState<string | undefined>(undefined);
  const { setSelectedSymbol } = useStore();

  const handleOpenChart = useCallback((symbol: string) => {
    setSelectedSymbol(symbol);
    setChartSymbol(symbol);
    setChartOpen(true);
  }, [setSelectedSymbol]);

  const handleCloseChart = useCallback(() => {
    setChartOpen(false);
  }, []);

  return (
    <>
      {/* Status bar — always visible at top */}
      <DayStatusBar date={selectedDate} onDateChange={setSelectedDate} onBriefingRun={() => setBriefingKey((k) => k + 1)} />

      {/* Two-column layout — fixed height with per-column scroll so status bar stays pinned */}
      <div className="grid grid-cols-12 gap-2 mt-2 h-[calc(100vh-96px)]">
        {/* Left: Watchlist */}
        <div className="col-span-8 flex flex-col gap-2 overflow-y-auto pr-1 pb-2">
          <Watchlist date={selectedDate} onOpenChart={handleOpenChart} />
          <PermanentWatchlist />
        </div>

        {/* Right: Agent Log + Global Cues */}
        <div className="col-span-4 flex flex-col gap-2 overflow-y-auto pb-2">
          <AgentLog date={selectedDate} />
          <SetupPerformance date={selectedDate} />
          <GlobalCues date={selectedDate} refreshKey={briefingKey} />
          <ConfigPanel />
        </div>
      </div>

      <ChartModal
        isOpen={chartOpen}
        onClose={handleCloseChart}
        initialSymbol={chartSymbol}
      />
    </>
  );
}

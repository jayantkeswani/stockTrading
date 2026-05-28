"use client";

import { useState, useCallback, useEffect } from "react";
import { ScannerPanel } from "@/components/dashboard/ScannerPanel";
import { ScannerHeader } from "@/components/dashboard/ScannerHeader";
import { ActivePositions } from "@/components/positions/ActivePositions";
import { PnLCard } from "@/components/dashboard/PnLCard";
import { Watchlist } from "@/components/dashboard/Watchlist";
import { Watchlist as FuturesWatchlist } from "@/components/intraday-futures/Watchlist";
import { ScanFeed } from "@/components/dashboard/ScanFeed";
import { AgentFeed } from "@/components/dashboard/AgentFeed";
import { ChartModal } from "@/components/charts/ChartModal";
import { useStore } from "@/store";
import { api } from "@/lib/api";
import { startOfDayIST, endOfDayIST } from "@/lib/formatters";
import type { AgentLog, Position, Signal, RiskDashboard, YoloProfile } from "@/lib/types";

export default function DashboardPage() {
  const [chartOpen, setChartOpen] = useState(false);
  const [chartSymbol, setChartSymbol] = useState<string | undefined>(undefined);
  const scannerShowExecuted = useStore((s) => s.scannerShowExecuted);

  // Load signals, open positions, risk dashboard, and agent logs on mount
  useEffect(() => {
    async function loadData() {
      try {
        const now = new Date();
        const dateRange = { generated_since: startOfDayIST(now).toISOString(), generated_until: endOfDayIST(now).toISOString() };
        const signalFetches: Promise<Signal[]>[] = [
          api.getSignals({ status: "PENDING", ...dateRange, limit: 50 }) as Promise<Signal[]>,
        ];
        if (scannerShowExecuted) {
          signalFetches.push(
            api.getSignals({ status: "EXECUTED", ...dateRange, limit: 50 }) as Promise<Signal[]>,
          );
        }
        const [pendingSignals, positions, risk, agentLogs, yoloProfiles, ...extraSignals] = await Promise.all([
          signalFetches[0],
          api.getPositions({}) as Promise<Position[]>,
          api.getRiskDashboard() as Promise<RiskDashboard>,
          api.getAgentLogs({ since: startOfDayIST(now).toISOString() }) as Promise<AgentLog[]>,
          api.getYoloProfiles() as Promise<YoloProfile[]>,
          ...(signalFetches.length > 1 ? [signalFetches[1]] : []),
        ]);
        const allSignals = extraSignals.length > 0
          ? [...pendingSignals, ...extraSignals[0]]
          : pendingSignals;
        const s = useStore.getState();
        s.setSignals(allSignals);
        s.setPositions(positions);
        s.setRisk(risk);
        s.setAgentLogs(agentLogs);
        s.setYoloProfiles(yoloProfiles);
      } catch {
        // API may not be running yet
      }
    }
    loadData();

    // Refresh risk dashboard every 30s for closed trade P&L updates
    const interval = setInterval(async () => {
      try {
        const riskData = (await api.getRiskDashboard()) as RiskDashboard;
        useStore.getState().setRisk(riskData);
      } catch {
        // ignore
      }
    }, 30_000);
    return () => clearInterval(interval);
  }, [scannerShowExecuted]);

  const handleOpenChart = useCallback(
    (symbol?: string) => {
      if (symbol) {
        useStore.getState().setSelectedSymbol(symbol);
        setChartSymbol(symbol);
      }
      setChartOpen(true);
    },
    []
  );

  const handleCloseChart = useCallback(() => {
    setChartOpen(false);
  }, []);

  return (
    <>
      {/* P&L strip — sticky at top of content area */}
      <div className="sticky top-0 z-10 pb-2 bg-bg-primary">
        <PnLCard />
      </div>

      <div className="grid grid-cols-12 gap-2 mt-2 h-[calc(100vh-96px)]">
        {/* LEFT: Main content */}
        <div className="col-span-8 flex flex-col gap-2 overflow-y-auto pr-1 pb-2">
          {/* Scanner Header — manual strategy scan triggers */}
          <ScannerHeader />

          {/* Scanner Panel — pending signals */}
          <ScannerPanel />

          {/* Active Positions */}
          <ActivePositions compact />

          {/* Futures Watchlist (Strategy 5 screener) */}
          <FuturesWatchlist onOpenChart={handleOpenChart} />
        </div>

        {/* RIGHT: Sidebar */}
        <div className="col-span-4 flex flex-col gap-2 overflow-y-auto pb-2">
          {/* Watchlist */}
          <Watchlist onOpenChart={handleOpenChart} />

          {/* Manual Scan Feed */}
          <ScanFeed />

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

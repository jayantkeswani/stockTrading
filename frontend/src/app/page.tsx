"use client";

import { useState, useCallback, useEffect } from "react";
import { ScannerPanel } from "@/components/dashboard/ScannerPanel";
import { ScannerHeader } from "@/components/dashboard/ScannerHeader";
import { ActivePositions } from "@/components/positions/ActivePositions";
import { PnLCard } from "@/components/dashboard/PnLCard";
import { Watchlist } from "@/components/dashboard/Watchlist";
import { ScanFeed } from "@/components/dashboard/ScanFeed";
import { AgentFeed } from "@/components/dashboard/AgentFeed";
import { ChartModal } from "@/components/charts/ChartModal";
import { useStore } from "@/store";
import { api } from "@/lib/api";
import { startOfDayIST, endOfDayIST } from "@/lib/formatters";
import type { AgentLog, Position, Signal, RiskDashboard } from "@/lib/types";

export default function DashboardPage() {
  const [chartOpen, setChartOpen] = useState(false);
  const [chartSymbol, setChartSymbol] = useState<string | undefined>(undefined);
  const { setSelectedSymbol, setSignals, setPositions, setRisk, setAgentLogs } = useStore();

  // Load pending signals, open positions, risk dashboard, and agent logs on mount
  useEffect(() => {
    async function loadData() {
      try {
        const now = new Date();
        const [signals, positions, risk, agentLogs] = await Promise.all([
          api.getSignals({ status: "PENDING", generated_since: startOfDayIST(now).toISOString(), generated_until: endOfDayIST(now).toISOString(), limit: 50 }) as Promise<Signal[]>,
          api.getPositions() as Promise<Position[]>,
          api.getRiskDashboard() as Promise<RiskDashboard>,
          api.getAgentLogs() as Promise<AgentLog[]>,
        ]);
        setSignals(signals);
        setPositions(positions);
        setRisk(risk);
        setAgentLogs(agentLogs);
      } catch {
        // API may not be running yet
      }
    }
    loadData();

    // Refresh risk dashboard every 30s for closed trade P&L updates
    const interval = setInterval(async () => {
      try {
        const riskData = (await api.getRiskDashboard()) as RiskDashboard;
        setRisk(riskData);
      } catch {
        // ignore
      }
    }, 30_000);
    return () => clearInterval(interval);
  }, [setSignals, setPositions, setRisk, setAgentLogs]);

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
      {/* P&L strip — full width at top */}
      <PnLCard />

      <div className="grid grid-cols-12 gap-2 mt-2 h-[calc(100vh-96px)]">
        {/* LEFT: Main content */}
        <div className="col-span-8 flex flex-col gap-2 overflow-y-auto pr-1 pb-2">
          {/* Scanner Header — manual strategy scan triggers */}
          <ScannerHeader />

          {/* Scanner Panel — pending signals */}
          <ScannerPanel />

          {/* Active Positions */}
          <ActivePositions compact />
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

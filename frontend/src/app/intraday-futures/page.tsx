"use client";

import { useState } from "react";
import { DayStatusBar } from "@/components/intraday-futures/DayStatusBar";
import { Watchlist } from "@/components/intraday-futures/Watchlist";
import { AgentLog } from "@/components/intraday-futures/AgentLog";
import { GlobalCues } from "@/components/intraday-futures/GlobalCues";
import { SetupPerformance } from "@/components/intraday-futures/SetupPerformance";
import { ConfigPanel } from "@/components/intraday-futures/ConfigPanel";

export default function IntradayFuturesPage() {
  const [selectedDate, setSelectedDate] = useState<string | null>(null);

  return (
    <div className="space-y-3">
      {/* Full-width status bar */}
      <DayStatusBar date={selectedDate} onDateChange={setSelectedDate} />

      {/* Two-column layout */}
      <div className="grid grid-cols-12 gap-3">
        {/* Left: Watchlist */}
        <div className="col-span-8 space-y-3">
          <Watchlist date={selectedDate} />
        </div>

        {/* Right: Agent Log + Global Cues */}
        <div className="col-span-4 space-y-3">
          <AgentLog date={selectedDate} />
          <SetupPerformance date={selectedDate} />
          <GlobalCues date={selectedDate} />
          <ConfigPanel />
        </div>
      </div>
    </div>
  );
}

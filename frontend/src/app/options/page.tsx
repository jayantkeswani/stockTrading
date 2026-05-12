"use client";

import { AgentLog } from "@/components/options/AgentLog";

export default function OptionsPage() {
  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h1 className="text-xs font-mono font-medium text-text-secondary uppercase tracking-wider">
          Options — VWAP Pullback
        </h1>
      </div>

      <div className="grid grid-cols-12 gap-3">
        <div className="col-span-8">
          <AgentLog date={null} />
        </div>
      </div>
    </div>
  );
}

"use client";

import { useStore } from "@/store";
import { useShallow } from "zustand/react/shallow";
import { formatINR } from "@/lib/formatters";

export function QuickStats() {
  const { risk, positions } = useStore(useShallow((s) => ({
    risk: s.risk,
    positions: s.positions,
  })));

  const stats = [
    {
      label: "Trades Today",
      value: `${risk?.trades_today ?? 0} / ${risk?.max_trades_per_day ?? 3}`,
    },
    {
      label: "Open Positions",
      value: `${positions.length}`,
    },
    {
      label: "Notional",
      value: formatINR(risk?.notional ?? 0),
    },
  ];

  return (
    <div className="rounded-lg border border-border bg-bg-secondary p-4">
      <div className="text-xs text-text-muted uppercase tracking-wider mb-3">
        Quick Stats
      </div>
      <div className="space-y-2">
        {stats.map((stat) => (
          <div key={stat.label} className="flex justify-between items-center">
            <span className="text-xs text-text-secondary">{stat.label}</span>
            <span className="text-sm font-mono text-text-primary">{stat.value}</span>
          </div>
        ))}
      </div>
      {risk?.is_halted && (
        <div className="mt-3 px-2 py-1 rounded bg-loss/20 text-loss text-xs text-center font-semibold">
          TRADING HALTED - Drawdown Limit
        </div>
      )}
    </div>
  );
}

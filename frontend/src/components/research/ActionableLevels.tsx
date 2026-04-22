"use client";

import { formatINR } from "@/lib/formatters";

interface ShortTermOpportunity {
  entry_zone_low?: number;
  entry_zone_high?: number;
  stop_loss?: number;
  target_1?: number;
  target_2?: number;
  risk_reward_ratio?: string;
  timeframe?: string;
}

export function ActionableLevels({
  shortTerm,
  longTermSuitability,
}: {
  shortTerm: ShortTermOpportunity | null;
  longTermSuitability?: string;
}) {
  if (!shortTerm || !shortTerm.entry_zone_low) return null;

  return (
    <div className="border border-accent/20 rounded bg-accent/5 p-3">
      <div className="text-[10px] font-mono text-accent uppercase tracking-wider mb-2 font-medium">
        Actionable Levels
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <div>
          <div className="text-[10px] font-mono text-text-muted">Entry Zone</div>
          <div className="text-xs font-mono text-text-primary">
            {formatINR(shortTerm.entry_zone_low)} - {formatINR(shortTerm.entry_zone_high || shortTerm.entry_zone_low)}
          </div>
        </div>
        <div>
          <div className="text-[10px] font-mono text-text-muted">Stop Loss</div>
          <div className="text-xs font-mono text-[#ff4060]">
            {shortTerm.stop_loss ? formatINR(shortTerm.stop_loss) : "N/A"}
          </div>
        </div>
        <div>
          <div className="text-[10px] font-mono text-text-muted">Target 1 / Target 2</div>
          <div className="text-xs font-mono text-[#00e68a]">
            {shortTerm.target_1 ? formatINR(shortTerm.target_1) : "N/A"}
            {shortTerm.target_2 ? ` / ${formatINR(shortTerm.target_2)}` : ""}
          </div>
        </div>
        <div>
          <div className="text-[10px] font-mono text-text-muted">R:R / Timeframe</div>
          <div className="text-xs font-mono text-text-primary">
            {shortTerm.risk_reward_ratio || "N/A"} / {shortTerm.timeframe || "N/A"}
          </div>
        </div>
      </div>
      {longTermSuitability && (
        <div className="mt-2 pt-2 border-t border-border">
          <span className="text-[10px] font-mono text-text-muted">Long-term: </span>
          <span className="text-[11px] font-mono text-text-secondary">{longTermSuitability}</span>
        </div>
      )}
    </div>
  );
}
